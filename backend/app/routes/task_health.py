"""Broken tasks and disagreement alerts (DC5, D43).

A worker who thinks a task is broken flags it instead of answering, with a reason, and gets
the next task. Two flags from different workers pause the task: it stops being routed and
waits for a researcher, who restores it or retires it. A retired task counts nowhere (metric
quality-v2). Gold tasks are flagged and paused the same way, so flagging never reveals which
tasks are gold, and nothing tells the worker what their flag did. A flag changes nothing in the
flagger's measures: no answer is recorded.

Separately, each upload batch's agreement is compared with what the same workers achieve on the
project's other tasks; a batch at least 8 points and 3 standard deviations below (z >= 3, since
every batch is checked again and again) raises an alert for researchers, listing its tasks with
the most disagreement, flagged or not.
"""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.audit import record
from app.db import get_session
from app.models import Assignment, Role, Task, User
from app.quality import SUBMISSION_FACTS
from app.routes.projects import get_visible_project
from app.scoping import require_roles
from app.work import mark_full

router = APIRouter()

FLAGS_TO_PAUSE = 2
MIN_BATCH = 30  # compared answers a batch needs before the alert judges it
MIN_OTHERS = 200  # compared answers on the rest of the project
DROP = 0.08
Z = 3.0  # stricter than D19's 2: every batch is checked, again and again, so noise would cross 2


class FlagIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(restore|retire)$")
    reason: str = Field(min_length=3, max_length=1000)


class BrokenTask(BaseModel):
    task_id: uuid.UUID
    payload: dict[str, Any]
    is_gold: bool
    broken_status: str
    answers: int
    flags: list[dict[str, Any]]


class FlaggerRow(BaseModel):
    handle: str
    vendor_name: str
    flags: int
    retired: int  # of their flags, how many were on tasks researchers retired


class AgreementAlert(BaseModel):
    batch_added_at: datetime
    batch_tasks: int
    agreement: float  # among the batch's compared answers
    others: float  # what the same workers achieve on the project's other tasks
    n: int
    others_n: int
    tasks: list[dict[str, Any]]  # the batch's tasks with the most disagreement


class TaskHealth(BaseModel):
    broken: list[BrokenTask]
    flaggers: list[FlaggerRow]
    agreement_alert: AgreementAlert | None


@router.post("/tasks/{task_id}/flag", status_code=status.HTTP_201_CREATED)
async def flag_task(task_id: uuid.UUID, body: FlagIn, user: User = Depends(require_roles(Role.WORKER)),
                    session: AsyncSession = Depends(get_session)) -> dict:
    """Flag the task you were given as broken. The assignment is handed back unanswered."""
    now = clock.now()
    assignment = await session.scalar(
        select(Assignment).where(Assignment.task_id == task_id, Assignment.worker_id == user.id, Assignment.status == "open",
                                 Assignment.expires_at > now).with_for_update())
    if assignment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "You don't have this task open")
    assignment.status = "flagged"
    await mark_full(session, task_id, assignment.vendor_id, False)  # the slot is free again, as when a lease expires
    await session.execute(
        text("INSERT INTO task_flags (task_id, worker_id, assignment_id, reason, created_at) VALUES (:t, :w, :a, :r, :now) "
             "ON CONFLICT ON CONSTRAINT task_flags_once_per_worker DO NOTHING"),
        {"t": task_id, "w": user.id, "a": assignment.id, "r": body.reason.strip(), "now": now},
    )
    state = (await session.execute(text("SELECT status, restored_at FROM broken_tasks WHERE task_id = :t FOR UPDATE"),
                                   {"t": task_id})).first()
    flags = await session.scalar(
        text("SELECT count(DISTINCT worker_id) FROM task_flags WHERE task_id = :t AND (CAST(:since AS timestamptz) IS NULL "
             "OR created_at > CAST(:since AS timestamptz))"), {"t": task_id, "since": state.restored_at if state else None})
    if (state is None or state.status == "restored") and flags >= FLAGS_TO_PAUSE:
        await session.execute(
            text("INSERT INTO broken_tasks (task_id, status, changed_at) VALUES (:t, 'paused', :now) "
                 "ON CONFLICT (task_id) DO UPDATE SET status = 'paused', changed_at = :now"), {"t": task_id, "now": now})
        # Anthropic's log only: a vendor seeing this could learn which tasks are gold.
        record(session, actor=None, action="task.paused", entity_type="task", entity_id=task_id, org_id=None,
               after={"flags": flags})
    await session.commit()
    return {}


@router.post("/tasks/{task_id}/broken-decision")
async def decide_broken(task_id: uuid.UUID, body: DecisionIn, user: User = Depends(require_roles(Role.RESEARCHER)),
                        session: AsyncSession = Depends(get_session)) -> dict:
    task = await session.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    await get_visible_project(task.project_id, user, session)
    now = clock.now()
    changed = await session.scalar(
        text("UPDATE broken_tasks SET status = :s, changed_at = :now, "
             "restored_at = CASE WHEN :s = 'restored' THEN :now ELSE restored_at END "
             "WHERE task_id = :t AND status = 'paused' RETURNING status"),
        {"s": "retired" if body.decision == "retire" else "restored", "now": now, "t": task_id})
    if changed is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a paused task can be restored or retired")
    record(session, actor=user, action=f"task.{body.decision}d", entity_type="task", entity_id=task_id, org_id=None,
           after={"reason": body.reason})
    await session.commit()
    return {"broken_status": None if changed == "restored" else changed}


BROKEN = """
SELECT t.id, t.payload, b.status AS broken_status, EXISTS (SELECT 1 FROM gold_answers g WHERE g.task_id = t.id) AS is_gold,
       (SELECT count(*) FROM submissions s WHERE s.task_id = t.id) AS answers,
       (SELECT json_agg(json_build_object('reason', f.reason, 'handle', u.handle, 'at', f.created_at) ORDER BY f.created_at)
        FROM task_flags f JOIN users u ON u.id = f.worker_id WHERE f.task_id = t.id) AS flags
FROM tasks t JOIN broken_tasks b ON b.task_id = t.id WHERE t.project_id = :p AND b.status <> 'restored'
ORDER BY b.status, t.seq
"""

FLAGGERS = """
SELECT u.handle, o.name AS vendor_name, count(*) AS flags, count(*) FILTER (WHERE b.status = 'retired') AS retired
FROM task_flags f JOIN tasks t ON t.id = f.task_id JOIN users u ON u.id = f.worker_id JOIN orgs o ON o.id = u.org_id
LEFT JOIN broken_tasks b ON b.task_id = t.id
WHERE t.project_id = :p GROUP BY u.handle, o.name ORDER BY count(*) DESC, u.handle
"""

COMPARED = f"""
SELECT f.agrees, f.worker_id, s.task_id, coalesce(t.batch_id::text, t.created_at::text) AS batch, t.created_at
FROM ({SUBMISSION_FACTS}) f JOIN submissions s ON s.id = f.id JOIN tasks t ON t.id = s.task_id
WHERE f.compared
"""


def unexpected_disagreement(observed: list[bool], expected: list[float]) -> tuple[float, float]:
    """How far observed agreement falls below what was expected of the same answers: the gap as a
    share of answers, and in standard deviations (a sum of independent yes/no chances)."""
    shortfall = sum(expected) - sum(observed)
    variance = sum(p * (1 - p) for p in expected)
    return shortfall / len(observed), (shortfall / variance ** 0.5 if variance else 0.0)


async def agreement_alert(session: AsyncSession, project_id: uuid.UUID) -> AgreementAlert | None:
    """The upload batch whose agreement falls furthest below what the same workers achieve on the
    project's other tasks, if the gap is significant. Measured against each worker's own agreement
    elsewhere, so who happened to be working, and a worker who disagrees everywhere (a matter for
    detection), don't set it off: only the tasks do (D43)."""
    rows = (await session.execute(text(COMPARED), {"project_id": project_id})).all()
    by_worker: dict[uuid.UUID, list[int]] = {}
    by_worker_batch: dict[tuple, list[int]] = {}
    batches: dict[str, list] = {}
    for r in rows:
        by_worker.setdefault(r.worker_id, [0, 0])
        by_worker[r.worker_id][0] += r.agrees
        by_worker[r.worker_id][1] += 1
        by_worker_batch.setdefault((r.worker_id, r.batch), [0, 0])
        by_worker_batch[(r.worker_id, r.batch)][0] += r.agrees
        by_worker_batch[(r.worker_id, r.batch)][1] += 1
        batches.setdefault(r.batch, []).append(r)
    worst = None
    for batch, answers in batches.items():
        if len(answers) < MIN_BATCH or len(rows) - len(answers) < MIN_OTHERS:
            continue
        expected = []
        for a in answers:  # the worker's agreement on the project's other batches, smoothed toward even odds
            k, n = by_worker[a.worker_id]
            kb, nb = by_worker_batch[(a.worker_id, batch)]
            expected.append((k - kb + 1) / (n - nb + 2))
        gap, z = unexpected_disagreement([a.agrees for a in answers], expected)
        if gap >= DROP and z >= Z and (worst is None or z > worst[1]):
            worst = (batch, z, answers, sum(expected) / len(expected))
    if worst is None:
        return None
    batch, _, answers, others = worst
    disagreements: dict[uuid.UUID, int] = {}
    for a in answers:
        if not a.agrees:
            disagreements[a.task_id] = disagreements.get(a.task_id, 0) + 1
    top = sorted(disagreements, key=lambda t: -disagreements[t])[:5]
    tasks = {r.id: r for r in (await session.execute(
        text("SELECT t.id, t.payload, b.status AS broken_status FROM tasks t LEFT JOIN broken_tasks b ON b.task_id = t.id "
             "WHERE t.id = ANY(:ids)"), {"ids": top})).all()}
    batch_tasks = await session.scalar(
        text("SELECT count(*) FROM tasks WHERE project_id = :p AND coalesce(batch_id::text, created_at::text) = :b"),
        {"p": project_id, "b": batch})
    return AgreementAlert(
        batch_added_at=answers[0].created_at, batch_tasks=batch_tasks,
        agreement=sum(a.agrees for a in answers) / len(answers), others=others, n=len(answers), others_n=len(rows) - len(answers),
        tasks=[{"task_id": t, "payload": tasks[t].payload, "disagreements": disagreements[t],
                "broken_status": tasks[t].broken_status} for t in top],
    )


@router.get("/projects/{project_id}/task-health")
async def task_health(project_id: uuid.UUID, user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)),
                      session: AsyncSession = Depends(get_session)) -> TaskHealth:
    project = await get_visible_project(project_id, user, session)
    broken = [BrokenTask(task_id=r.id, payload=r.payload, is_gold=r.is_gold, broken_status=r.broken_status, answers=r.answers,
                         flags=r.flags or []) for r in await session.execute(text(BROKEN), {"p": project.id})]
    flaggers = [FlaggerRow(**r._mapping) for r in await session.execute(text(FLAGGERS), {"p": project.id})]
    return TaskHealth(broken=broken, flaggers=flaggers, agreement_alert=await agreement_alert(session, project.id))
