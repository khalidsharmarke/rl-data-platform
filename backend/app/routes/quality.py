import uuid
from datetime import datetime
from typing import Any

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, grades
from app.audit import record
from app.db import get_session
from app.models import GoldAnswer, GoldRetirement, Org, Review, Role, Submission, Task, TaskSchema, User
from app.quality import METRIC_VERSION, measures_by
from app.routes.projects import TaskIn, get_visible_project
from app.scoping import require_roles
from app.work import route_tasks

router = APIRouter()


class QualityRow(BaseModel):
    id: uuid.UUID
    label: str
    submissions: int
    gold_n: int
    gold_accuracy: float | None
    compared: int
    agreement: float | None
    reviewed: int
    rejected: int
    accepted: int


class QualityOut(BaseModel):
    metric_version: str
    vendors: list[QualityRow]
    workers: list[QualityRow]


class GradeRow(BaseModel):
    worker_id: uuid.UUID
    handle: str
    vendor_name: str
    gold_n: int
    gold_accuracy: float | None
    interval: tuple[float, float] | None
    quality: str
    integrity: str
    later_gold_needed: int | None
    watch_progress: tuple[int, int] | None


class GradesOut(BaseModel):
    version: str
    quality_bar: float
    workers: list[GradeRow]


class EscalationRow(BaseModel):
    id: uuid.UUID
    handle: str
    vendor_name: str
    rule: str
    started_at: datetime
    ended_at: datetime | None
    outcome: str | None
    gold_since: int
    gold_right_since: int


class ReviewItem(BaseModel):
    submission_id: uuid.UUID
    vendor_name: str
    worker_handle: str
    submitted_at: datetime
    payload: dict[str, Any]
    answer: dict[str, Any]


class ReviewIn(BaseModel):
    verdict: str = Field(pattern="^(accepted|rejected)$")
    reason: str | None = Field(default=None, max_length=1000)


class FeedbackItem(BaseModel):
    submission_id: uuid.UUID
    project_name: str
    prompt: str
    your_answer: dict[str, Any]
    reason: str
    reviewed_at: datetime


class GoldSetIn(BaseModel):
    tasks: list[TaskIn] = Field(min_length=1, max_length=1000)
    reason: str = Field(default="Scheduled rotation", min_length=1)


@router.get("/projects/{project_id}/quality")
async def project_quality(
    project_id: uuid.UUID,
    user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> QualityOut:
    project = await get_visible_project(project_id, user, session)
    vendors = await measures_by(session, project.id, "vendor_id")
    workers = await measures_by(session, project.id, "worker_id")
    names = dict((await session.execute(select(Org.id, Org.name).where(Org.id.in_(vendors)))).all())
    handles = dict((await session.execute(select(User.id, User.handle).where(User.id.in_(workers)))).all())
    row = lambda key, label, m: QualityRow(id=key, label=label, **{k: v for k, v in m.as_dict().items() if k in QualityRow.model_fields})  # noqa: E731
    return QualityOut(
        metric_version=METRIC_VERSION,
        vendors=sorted((row(k, names[k], m) for k, m in vendors.items()), key=lambda r: r.label),
        workers=sorted((row(k, handles[k], m) for k, m in workers.items()), key=lambda r: r.label),
    )


@router.get("/projects/{project_id}/grades")
async def project_grades(
    project_id: uuid.UUID,
    user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> GradesOut:
    """Each worker's quality and integrity grades (DC11). Anthropic only; workers appear by
    pseudonymous handle (D12)."""
    project = await get_visible_project(project_id, user, session)
    graded = await grades.compute(session, project)
    who = (await session.execute(
        select(User.id, User.handle, Org.name).join(Org, Org.id == User.org_id).where(User.id.in_(graded))
    )).all()
    names = {w.id: (w.handle, w.name) for w in who}
    rows = [
        GradeRow(worker_id=g.worker_id, handle=names[g.worker_id][0], vendor_name=names[g.worker_id][1], gold_n=g.gold_n,
                 gold_accuracy=g.gold_correct / g.gold_n if g.gold_n else None, interval=g.interval, quality=g.quality,
                 integrity=g.integrity, later_gold_needed=g.later_gold_needed, watch_progress=g.watch_progress)
        for g in graded.values()
    ]
    return GradesOut(version=grades.GRADES_VERSION, quality_bar=float(project.quality_bar),
                     workers=sorted(rows, key=lambda r: (r.vendor_name, r.handle)))


@router.get("/projects/{project_id}/escalations")
async def project_escalations(
    project_id: uuid.UUID,
    user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> list[EscalationRow]:
    """Workers measured more closely after a watch finding, and how each escalation ended (AB14).
    Anthropic only: the worker and their vendor never learn of it."""
    project = await get_visible_project(project_id, user, session)
    rows = await session.execute(
        text(
            """
            SELECT x.id, u.handle, o.name AS vendor_name, f.rule, x.started_at, x.ended_at, x.outcome,
                   count(g.task_id) AS gold_since, count(g.task_id) FILTER (WHERE s.answer @> g.answer) AS gold_right_since
            FROM escalations x
            JOIN users u ON u.id = x.worker_id
            JOIN orgs o ON o.id = u.org_id
            JOIN findings f ON f.id = x.finding_id
            LEFT JOIN submission_events e ON e.worker_id = x.worker_id AND e.project_id = x.project_id AND e.id > x.after_event
            LEFT JOIN submissions s ON s.id = e.submission_id AND (x.ended_at IS NULL OR s.submitted_at <= x.ended_at)
            LEFT JOIN gold_answers g ON g.task_id = s.task_id
            WHERE x.project_id = :p
            GROUP BY x.id, u.handle, o.name, f.rule
            ORDER BY x.started_at
            """
        ),
        {"p": project.id},
    )
    return [EscalationRow(**r._mapping) for r in rows]


@router.get("/projects/{project_id}/review-queue")
async def review_queue(
    project_id: uuid.UUID,
    n: int = Query(10, ge=1, le=50),
    user: User = Depends(require_roles(Role.RESEARCHER)),
    session: AsyncSession = Depends(get_session),
) -> list[ReviewItem]:
    """A random sample of unreviewed answers on regular tasks: researcher spot checks (DC4)."""
    project = await get_visible_project(project_id, user, session)
    rows = await session.execute(
        text(
            """
            SELECT s.id, o.name AS vendor_name, u.handle, s.submitted_at, t.payload, s.answer
            FROM submissions s
            JOIN tasks t ON t.id = s.task_id
            JOIN orgs o ON o.id = s.vendor_id
            JOIN users u ON u.id = s.worker_id
            WHERE s.project_id = :p
              AND NOT EXISTS (SELECT 1 FROM gold_answers g WHERE g.task_id = s.task_id)
              AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.submission_id = s.id)
            ORDER BY random()
            LIMIT :n
            """
        ),
        {"p": project.id, "n": n},
    )
    return [
        ReviewItem(submission_id=r.id, vendor_name=r.vendor_name, worker_handle=r.handle, submitted_at=r.submitted_at, payload=r.payload, answer=r.answer)
        for r in rows
    ]


@router.post("/submissions/{submission_id}/review", status_code=status.HTTP_201_CREATED)
async def review_submission(
    submission_id: uuid.UUID,
    body: ReviewIn,
    user: User = Depends(require_roles(Role.RESEARCHER)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    submission = await session.get(Submission, submission_id)
    if submission is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    reason = (body.reason or "").strip() or None
    if body.verdict == "rejected" and reason is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A rejection needs a reason the worker can learn from")
    review = Review(submission_id=submission.id, reviewer_id=user.id, verdict=body.verdict, reason=reason, created_at=clock.now())
    session.add(review)
    await session.flush()
    record(session, actor=user, action=f"submission.{body.verdict}", entity_type="submission", entity_id=submission.id,
           org_id=submission.vendor_id, after={"review_id": str(review.id), "reason": reason})
    await session.commit()
    return {"review_id": str(review.id), "verdict": review.verdict}


@router.get("/me/feedback")
async def my_feedback(user: User = Depends(require_roles(Role.WORKER)), session: AsyncSession = Depends(get_session)) -> list[FeedbackItem]:
    """Reviewer rejections with reasons. Gold results are never shown per task (PD2)."""
    rows = await session.execute(
        text(
            """
            SELECT DISTINCT ON (s.id) s.id, p.name AS project_name, t.payload->>'prompt' AS prompt, s.answer,
                   r.verdict, r.reason, r.created_at
            FROM submissions s
            JOIN reviews r ON r.submission_id = s.id
            JOIN tasks t ON t.id = s.task_id
            JOIN projects p ON p.id = s.project_id
            WHERE s.worker_id = :w
            ORDER BY s.id, r.created_at DESC, r.id
            """
        ),
        {"w": user.id},
    )
    items = [
        FeedbackItem(submission_id=r.id, project_name=r.project_name, prompt=r.prompt or "", your_answer=r.answer, reason=r.reason, reviewed_at=r.created_at)
        for r in rows
        if r.verdict == "rejected"
    ]
    return sorted(items, key=lambda i: i.reviewed_at, reverse=True)


@router.post("/projects/{project_id}/gold-sets", status_code=status.HTTP_201_CREATED)
async def rotate_gold(
    project_id: uuid.UUID,
    body: GoldSetIn,
    user: User = Depends(require_roles(Role.RESEARCHER)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Retire the project's active gold tasks and add a new gold set (PD2)."""
    project = await get_visible_project(project_id, user, session)
    schema = await session.get(TaskSchema, (project.id, project.current_schema_version))
    validator = jsonschema.Draft202012Validator(schema.answer_schema)
    if any(t.gold_answer is None or not validator.is_valid(t.gold_answer) for t in body.tasks):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Every task in a gold set needs a gold answer that fits the schema")

    project_gold = select(GoldAnswer.task_id).join(Task, Task.id == GoldAnswer.task_id).where(Task.project_id == project.id)
    active = (await session.scalars(project_gold.where(GoldAnswer.task_id.not_in(select(GoldRetirement.task_id))))).all()
    for task_id in active:
        session.add(GoldRetirement(task_id=task_id, retired_by=user.id, reason=body.reason, retired_at=clock.now()))
    version = (await session.scalar(select(func.max(GoldAnswer.gold_set_version)).where(GoldAnswer.task_id.in_(project_gold)))) or 0
    for t in body.tasks:
        task = Task(project_id=project.id, schema_version=project.current_schema_version, payload=t.payload)
        session.add(task)
        await session.flush()
        session.add(GoldAnswer(task_id=task.id, answer=t.gold_answer, gold_set_version=version + 1))
    await session.flush()
    await route_tasks(session, project)
    record(session, actor=user, action="gold.rotated", entity_type="project", entity_id=project.id, org_id=user.org_id,
           after={"retired": len(active), "added": len(body.tasks), "gold_set_version": version + 1, "reason": body.reason})
    await session.commit()
    return {"retired": len(active), "added": len(body.tasks), "gold_set_version": version + 1}
