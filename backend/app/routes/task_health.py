"""Broken tasks and disagreement alerts (DC5, D43).

A worker who thinks a task is broken flags it instead of answering, with a reason, and gets
the next task. Two flags from different workers pause the task: it stops being routed and
waits for a researcher, who restores it or retires it. A retired task counts nowhere (metric
quality-v2). Gold tasks are flagged and paused the same way, so flagging never reveals which
tasks are gold, and nothing tells the worker what their flag did. A flag changes nothing in the
flagger's measures: no answer is recorded.

Separately, a project's agreement over its latest answers is compared with its earlier
agreement (the same two-proportion test as D19); a significant drop raises an alert for
researchers, listing the tasks with the most disagreement.
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
from app.scorecards import significant_drop
from app.scoping import require_roles
from app.work import mark_full

router = APIRouter()

FLAGS_TO_PAUSE = 2
RECENT = 300  # compared answers in the recent window of the agreement alert
MIN_SIDE = 200  # compared answers each side needs before the alert can fire
DROP = 0.08


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
    recent: float
    baseline: float
    recent_n: int
    baseline_n: int
    tasks: list[dict[str, Any]]


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
SELECT f.id, f.agrees, s.task_id FROM ({SUBMISSION_FACTS}) f JOIN submissions s ON s.id = f.id
WHERE f.compared ORDER BY f.submitted_at, s.id
"""


async def agreement_alert(session: AsyncSession, project_id: uuid.UUID) -> AgreementAlert | None:
    rows = (await session.execute(text(COMPARED), {"project_id": project_id})).all()
    if len(rows) < RECENT + MIN_SIDE:
        return None
    before, recent = rows[:-RECENT], rows[-RECENT:]
    r_hits, b_hits = sum(r.agrees for r in recent), sum(r.agrees for r in before)
    if not significant_drop(r_hits, len(recent), b_hits, len(before), drop=DROP, z=2.0):
        return None
    disagreements: dict[uuid.UUID, int] = {}
    for r in recent:
        if not r.agrees:
            disagreements[r.task_id] = disagreements.get(r.task_id, 0) + 1
    worst = sorted(disagreements, key=lambda t: -disagreements[t])[:5]
    prompts = {r.id: r for r in (await session.execute(
        text("SELECT t.id, t.payload, b.status AS broken_status FROM tasks t LEFT JOIN broken_tasks b ON b.task_id = t.id "
             "WHERE t.id = ANY(:ids)"), {"ids": worst})).all()}
    return AgreementAlert(
        recent=r_hits / len(recent), baseline=b_hits / len(before), recent_n=len(recent), baseline_n=len(before),
        tasks=[{"task_id": t, "payload": prompts[t].payload, "disagreements": disagreements[t],
                "broken_status": prompts[t].broken_status} for t in worst],
    )


@router.get("/projects/{project_id}/task-health")
async def task_health(project_id: uuid.UUID, user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)),
                      session: AsyncSession = Depends(get_session)) -> TaskHealth:
    project = await get_visible_project(project_id, user, session)
    broken = [BrokenTask(task_id=r.id, payload=r.payload, is_gold=r.is_gold, broken_status=r.broken_status, answers=r.answers,
                         flags=r.flags or []) for r in await session.execute(text(BROKEN), {"p": project.id})]
    flaggers = [FlaggerRow(**r._mapping) for r in await session.execute(text(FLAGGERS), {"p": project.id})]
    return TaskHealth(broken=broken, flaggers=flaggers, agreement_alert=await agreement_alert(session, project.id))
