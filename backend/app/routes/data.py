"""Lineage, clean-up, the release stage and exports (AB6, DC6, DC10, D14)."""

import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, release
from app.abuse import add_action
from app.audit import record
from app.config import settings
from app.db import get_session
from app.models import Project, Role, User
from app.quality import METRIC_VERSION, SUBMISSION_FACTS
from app.routes.projects import get_visible_project
from app.scoping import require_roles
from app.work import mark_full

router = APIRouter()


class DatasetUse(BaseModel):
    dataset_id: uuid.UUID
    created_at: datetime
    rows_from_subject: int
    current: bool


class LineageOut(BaseModel):
    worker_id: uuid.UUID
    handle: str
    vendor_name: str
    submissions: int
    held: int
    removed: int
    accepted_now: int
    datasets: list[DatasetUse]


class CleanupIn(BaseModel):
    case_id: uuid.UUID
    mode: str = "relabel"


class CleanupOut(BaseModel):
    id: uuid.UUID
    removed: int
    tasks_reopened: int
    datasets_affected: int


class DatasetOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    created_at: datetime
    metric_version: str
    row_count: int
    provisional: int
    supersedes: uuid.UUID | None


@router.get("/lineage")
async def lineage(
    worker_id: uuid.UUID = Query(),
    project_id: uuid.UUID = Query(),
    user: User = Depends(require_roles(Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> LineageOut:
    """Everything one worker produced on a project, and where it went (AB6, D14)."""
    await get_visible_project(project_id, user, session)
    who = (await session.execute(
        text("SELECT u.handle, o.name FROM users u JOIN orgs o ON o.id = u.org_id WHERE u.id = :w"), {"w": worker_id}
    )).first()
    if who is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    counts = (await session.execute(
        text(f"SELECT count(*) n, count(*) FILTER (WHERE held) held, count(*) FILTER (WHERE removed) removed, "
             f"count(*) FILTER (WHERE accepted) accepted FROM ({SUBMISSION_FACTS}) f WHERE worker_id = :w"),
        {"project_id": project_id, "w": worker_id},
    )).one()
    latest = await session.scalar(text("SELECT id FROM datasets WHERE project_id = :p ORDER BY created_at DESC LIMIT 1"), {"p": project_id})
    uses = await session.execute(
        text(
            "SELECT d.id, d.created_at, count(*) n FROM dataset_rows r JOIN datasets d ON d.id = r.dataset_id "
            "JOIN submissions s ON s.id = r.submission_id WHERE s.worker_id = :w AND d.project_id = :p "
            "GROUP BY d.id, d.created_at ORDER BY d.created_at"
        ),
        {"w": worker_id, "p": project_id},
    )
    return LineageOut(
        worker_id=worker_id, handle=who.handle, vendor_name=who.name, submissions=counts.n, held=counts.held,
        removed=counts.removed, accepted_now=counts.accepted,
        datasets=[DatasetUse(dataset_id=u.id, created_at=u.created_at, rows_from_subject=u.n, current=u.id == latest) for u in uses],
    )


@router.post("/cleanups", status_code=status.HTTP_201_CREATED)
async def cleanup(body: CleanupIn, user: User = Depends(require_roles(Role.DATA_OPS)), session: AsyncSession = Depends(get_session)) -> CleanupOut:
    """Remove a suspended or removed worker's answers (AB6). Re-label also reopens their tasks."""
    if body.mode not in ("discard", "relabel"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Mode must be discard or relabel")
    case = (await session.execute(text("SELECT * FROM cases WHERE id = :c"), {"c": body.case_id})).first()
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if case.subject_type != "worker":
        raise HTTPException(status.HTTP_409_CONFLICT, "Clean-up removes a worker's answers; an environment is quarantined instead")
    if case.status != "decided" or case.decision not in ("suspend", "remove"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Clean-up follows a decision to suspend or remove the worker")
    if await session.scalar(text("SELECT 1 FROM cleanups WHERE case_id = :c"), {"c": case.id}):
        raise HTTPException(status.HTTP_409_CONFLICT, "This case has already been cleaned up")
    now = clock.now()
    cleanup_id = uuid.uuid4()
    targets = (await session.execute(
        text("SELECT s.id, s.task_id, s.vendor_id FROM submissions s WHERE s.worker_id = :w "
             "AND NOT EXISTS (SELECT 1 FROM removals rm WHERE rm.submission_id = s.id)"),
        {"w": case.subject_id},
    )).all()
    requeue = body.mode == "relabel"
    await session.execute(
        text("INSERT INTO cleanups (id, case_id, worker_id, mode, requested_by, created_at, removed, tasks_reopened) "
             "VALUES (:id, :c, :w, :m, :u, :now, :n, :r)"),
        {"id": cleanup_id, "c": case.id, "w": case.subject_id, "m": body.mode, "u": user.id, "now": now,
         "n": len(targets), "r": len(targets) if requeue else 0},
    )
    for t in targets:
        await session.execute(text("INSERT INTO removals (submission_id, cleanup_id, requeue) VALUES (:s, :c, :q)"),
                              {"s": t.id, "c": cleanup_id, "q": requeue})
        if requeue:
            await mark_full(session, t.task_id, t.vendor_id, False)
    affected = await session.scalar(
        text("SELECT count(DISTINCT r.dataset_id) FROM dataset_rows r JOIN removals rm ON rm.submission_id = r.submission_id "
             "WHERE rm.cleanup_id = :c"), {"c": cleanup_id})
    await session.execute(text("UPDATE holds SET released_at = :now WHERE case_id = :c AND released_at IS NULL"), {"now": now, "c": case.id})
    note = f"{len(targets)} answers removed" + (", tasks reopened for re-labeling" if requeue else "") + f"; {affected} earlier dataset(s) contained them"
    await add_action(session, case.id, "cleanup", user.id, note, now)
    await add_action(session, case.id, "release", user.id, "Hold released: the answers are removed", now)
    record(session, actor=user, action="cleanup.done", entity_type="case", entity_id=case.id, org_id=case.vendor_id,
           after={"mode": body.mode, "removed": len(targets), "datasets_affected": affected})
    await session.commit()
    return CleanupOut(id=cleanup_id, removed=len(targets), tasks_reopened=len(targets) if requeue else 0, datasets_affected=affected)


EXPORT_ROWS = """
SELECT s.id AS submission_id, s.task_id, t.payload, coalesce(vr.corrected_answer, s.answer) AS answer,
       vr.decision AS vendor_review, rv.handle AS reviewer, s.schema_version, s.submitted_at, u.handle, o.name AS vendor
FROM submissions s
JOIN tasks t ON t.id = s.task_id
LEFT JOIN vendor_reviews vr ON vr.submission_id = s.id
LEFT JOIN users rv ON rv.id = vr.reviewer_id
JOIN users u ON u.id = s.worker_id
JOIN orgs o ON o.id = s.vendor_id
WHERE s.id = ANY(CAST(:ids AS uuid[]))
ORDER BY s.submitted_at, s.id
"""


class ReleaseOut(BaseModel):
    accepted: int
    verified: int
    provisional: int
    waiting: dict[str, int]
    detection_through: int
    as_of: datetime
    release_after_minutes: int
    release_cap_minutes: int


@router.get("/projects/{project_id}/release")
async def release_summary(
    project_id: uuid.UUID, user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)), session: AsyncSession = Depends(get_session)
) -> ReleaseOut:
    """How much accepted data is released, and what the rest is waiting for (DC10)."""
    project: Project = await get_visible_project(project_id, user, session)
    view = await release.evaluate(session, project)
    return ReleaseOut(**view.summary(), release_after_minutes=project.release_after_minutes,
                      release_cap_minutes=project.release_cap_minutes)


@router.post("/projects/{project_id}/exports", status_code=status.HTTP_201_CREATED)
async def export(
    project_id: uuid.UUID, user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)), session: AsyncSession = Depends(get_session)
) -> DatasetOut:
    """Write released answers to a dataset file, each row with its lineage and tier (DC6, DC10, D14, D31)."""
    project: Project = await get_visible_project(project_id, user, session)
    if project.onboarding:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Onboarding samples are never exported (D46)")
    view = await release.evaluate(session, project)
    tiers = {d.submission_id: d.state for d in view.released()}
    snapshots = {d.submission_id: view.grades[d.worker_id].snapshot() for d in view.released()}
    rows = (await session.execute(text(EXPORT_ROWS), {"ids": list(tiers)})).all()
    previous = await session.scalar(text("SELECT id FROM datasets WHERE project_id = :p ORDER BY created_at DESC LIMIT 1"), {"p": project.id})
    dataset_id = uuid.uuid4()
    path = Path(settings.export_dir) / f"{dataset_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as out:
        for r in rows:
            out.write(json.dumps({
                "submission_id": str(r.submission_id), "task_id": str(r.task_id), "task": r.payload, "answer": r.answer,
                "worker": r.handle, "vendor": r.vendor, "schema_version": r.schema_version, "metric_version": METRIC_VERSION,
                "submitted_at": r.submitted_at.isoformat(), "tier": tiers[r.submission_id],
                "worker_grade": snapshots[r.submission_id],
                # The answer the vendor submitted; the worker's own, if corrected, is in the submission's lineage (D44).
                "vendor_review": {"decision": r.vendor_review, "reviewer": r.reviewer} if r.vendor_review else None,
            }) + "\n")
    provisional = sum(1 for r in rows if tiers[r.submission_id] == "provisional")
    now = clock.now()
    await session.execute(
        text("INSERT INTO datasets (id, project_id, created_by, created_at, metric_version, row_count, uri, supersedes) "
             "VALUES (:id, :p, :u, :now, :m, :n, :uri, :prev)"),
        {"id": dataset_id, "p": project.id, "u": user.id, "now": now, "m": METRIC_VERSION, "n": len(rows), "uri": str(path), "prev": previous},
    )
    if rows:
        await session.execute(
            text("INSERT INTO dataset_rows (dataset_id, submission_id, tier, grades) "
                 "SELECT :d, unnest(CAST(:ids AS uuid[])), unnest(CAST(:tiers AS text[])), unnest(CAST(:grades AS jsonb[]))"),
            {"d": dataset_id, "ids": [r.submission_id for r in rows], "tiers": [tiers[r.submission_id] for r in rows],
             "grades": [json.dumps(snapshots[r.submission_id]) for r in rows]},
        )
    record(session, actor=user, action="dataset.exported", entity_type="dataset", entity_id=dataset_id, org_id=user.org_id,
           after={"project_id": str(project.id), "rows": len(rows), "provisional": provisional,
                  "supersedes": str(previous) if previous else None})
    await session.commit()
    return DatasetOut(id=dataset_id, project_id=project.id, created_at=now, metric_version=METRIC_VERSION, row_count=len(rows),
                      provisional=provisional, supersedes=previous)


@router.get("/projects/{project_id}/datasets")
async def datasets(
    project_id: uuid.UUID, user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)), session: AsyncSession = Depends(get_session)
) -> list[DatasetOut]:
    await get_visible_project(project_id, user, session)
    rows = await session.execute(text(
        "SELECT d.*, (SELECT count(*) FROM dataset_rows r WHERE r.dataset_id = d.id AND r.tier = 'provisional') AS provisional "
        "FROM datasets d WHERE d.project_id = :p ORDER BY d.created_at DESC"), {"p": project_id})
    return [DatasetOut(id=r.id, project_id=r.project_id, created_at=r.created_at, metric_version=r.metric_version,
                       row_count=r.row_count, provisional=r.provisional, supersedes=r.supersedes) for r in rows]


@router.get("/datasets/{dataset_id}/download")
async def download(
    dataset_id: uuid.UUID, user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)), session: AsyncSession = Depends(get_session)
) -> FileResponse:
    uri = await session.scalar(text("SELECT uri FROM datasets WHERE id = :d"), {"d": dataset_id})
    if uri is None or not Path(uri).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return FileResponse(uri, media_type="application/jsonl", filename=f"dataset-{dataset_id}.jsonl")
