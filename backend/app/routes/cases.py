import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.abuse import add_action
from app.audit import record
from app.db import get_session
from app.models import Role, User
from app.scoping import is_internal, require_roles

router = APIRouter(prefix="/cases")

DECISIONS = {"clear", "warn", "suspend", "remove"}


class CaseSummary(BaseModel):
    id: uuid.UUID
    vendor_name: str
    subject_type: str
    subject_handle: str
    subject_name: str | None
    owner: str
    status: str
    opened_at: datetime
    decision: str | None
    decided_at: datetime | None
    held: bool
    observations: list[str]


class Flag(BaseModel):
    observation: str
    evidence: dict[str, Any]
    rule: str | None  # Anthropic only (N17)
    rule_version: str | None


class Sample(BaseModel):
    submission_id: uuid.UUID
    prompt: str
    answer: dict[str, Any]
    time_on_task_s: float
    pasted: bool


class Action(BaseModel):
    action: str
    by: str
    note: str | None
    at: datetime


class CaseDetail(CaseSummary):
    decision_reason: str | None
    flags: list[Flag]
    samples: list[Sample]
    actions: list[Action]


class DecisionIn(BaseModel):
    decision: str
    reason: str = Field(min_length=3, max_length=1000)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


CASES = """
SELECT c.*, o.name AS vendor_name, u.handle, u.display_name, u.org_id AS subject_org,
       EXISTS (SELECT 1 FROM holds h WHERE h.case_id = c.id AND h.released_at IS NULL) AS held,
       coalesce((SELECT array_agg(f.observation ORDER BY f.created_at) FROM flags f WHERE f.case_id = c.id), '{}') AS observations
FROM cases c
JOIN orgs o ON o.id = c.vendor_id
LEFT JOIN users u ON u.id = c.subject_id
"""


def summary(row, viewer: User) -> dict:
    # Workers are pseudonymous outside their vendor (D12).
    name = row.display_name if row.subject_org == viewer.org_id else None
    return dict(
        id=row.id, vendor_name=row.vendor_name, subject_type=row.subject_type, subject_handle=row.handle or "—",
        subject_name=name, owner=row.owner, status=row.status, opened_at=row.opened_at, decision=row.decision,
        decided_at=row.decided_at, held=row.held, observations=list(row.observations),
    )


async def visible_case(session: AsyncSession, case_id: uuid.UUID, user: User):
    row = (await session.execute(text(CASES + " WHERE c.id = :id"), {"id": case_id})).first()
    if row is None or not (is_internal(user) or row.vendor_id == user.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return row


def owns(row, user: User) -> bool:
    if row.owner == "vendor_admin":
        return user.role == Role.VENDOR_ADMIN and user.org_id == row.vendor_id
    return user.role == Role.DATA_OPS


@router.get("")
async def list_cases(
    status_: str | None = Query(None, alias="status"),
    user: User = Depends(require_roles(Role.VENDOR_ADMIN, Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> list[CaseSummary]:
    """Vendor admins see their own vendor's cases; data ops sees every case and its outcome (AB7)."""
    where, params = [], {}
    if not is_internal(user):
        where.append("c.vendor_id = :v")
        params["v"] = user.org_id
    if status_:
        where.append("c.status = :s")
        params["s"] = status_
    query = CASES + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY c.opened_at DESC"
    return [CaseSummary(**summary(r, user)) for r in await session.execute(text(query), params)]


@router.get("/{case_id}")
async def case_detail(
    case_id: uuid.UUID, user: User = Depends(require_roles(Role.VENDOR_ADMIN, Role.DATA_OPS)), session: AsyncSession = Depends(get_session)
) -> CaseDetail:
    row = await visible_case(session, case_id, user)
    internal = is_internal(user)
    flags = [
        Flag(observation=f.observation, evidence=f.evidence, rule=f.rule if internal else None, rule_version=f.rule_version if internal else None)
        for f in await session.execute(text("SELECT * FROM flags WHERE case_id = :c ORDER BY created_at"), {"c": case_id})
    ]
    ids = list({i for f in flags for i in f.evidence.get("submission_ids", [])})[:6]
    samples = [
        Sample(submission_id=s.id, prompt=s.prompt or "", answer=s.answer, time_on_task_s=round(s.time_on_task_ms / 1000, 1), pasted=bool(s.pasted))
        for s in await session.execute(
            text(
                "SELECT s.id, t.payload->>'prompt' AS prompt, s.answer, s.time_on_task_ms, (s.client_signals->>'pasted')::boolean AS pasted "
                "FROM submissions s JOIN tasks t ON t.id = s.task_id WHERE s.id = ANY(CAST(:ids AS uuid[])) ORDER BY s.submitted_at"
            ),
            {"ids": ids},
        )
    ]
    actions = [
        Action(action=a.action, by=("Platform" if a.actor_id is None else ("You" if a.actor_id == user.id else a.role.replace("_", " ").title())), note=a.note, at=a.created_at)
        for a in await session.execute(
            text("SELECT a.*, u.role FROM case_actions a LEFT JOIN users u ON u.id = a.actor_id WHERE a.case_id = :c ORDER BY a.seq"), {"c": case_id}
        )
    ]
    return CaseDetail(**summary(row, user), decision_reason=row.decision_reason, flags=flags, samples=samples, actions=actions)


@router.post("/{case_id}/decision")
async def decide(
    case_id: uuid.UUID,
    body: DecisionIn,
    user: User = Depends(require_roles(Role.VENDOR_ADMIN, Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> CaseDetail:
    """A person decides (D16). Clear and warn release the hold; suspend and remove keep the
    worker's answers held for data ops' clean-up and stop the worker taking new tasks."""
    row = await visible_case(session, case_id, user)
    if not owns(row, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the case owner decides this case")
    if row.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "This case is already decided; reverse it instead")
    if body.decision not in DECISIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Decision must be one of {sorted(DECISIONS)}")
    now = clock.now()
    await session.execute(
        text("UPDATE cases SET status = 'decided', decision = :d, decision_reason = :r, decided_by = :u, decided_at = :now WHERE id = :c"),
        {"d": body.decision, "r": body.reason, "u": user.id, "now": now, "c": case_id},
    )
    await add_action(session, case_id, body.decision, user.id, body.reason, now)
    if body.decision in ("clear", "warn"):
        await session.execute(text("UPDATE holds SET released_at = :now WHERE case_id = :c AND released_at IS NULL"), {"now": now, "c": case_id})
        await add_action(session, case_id, "release", user.id, "Hold released with the decision", now)
    else:
        await session.execute(text("UPDATE users SET suspended_at = :now WHERE id = :w"), {"now": now, "w": row.subject_id})
    record(session, actor=user, action=f"case.{body.decision}", entity_type="case", entity_id=case_id, org_id=row.vendor_id,
           after={"decision": body.decision, "reason": body.reason, "worker_id": str(row.subject_id)})
    await session.commit()
    return await case_detail(case_id, user, session)


@router.post("/{case_id}/reverse")
async def reverse(
    case_id: uuid.UUID,
    body: ReverseIn,
    user: User = Depends(require_roles(Role.VENDOR_ADMIN, Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> CaseDetail:
    """Undo a decision (N18): reinstate the worker and release any hold. The decision stays on record."""
    row = await visible_case(session, case_id, user)
    if not (owns(row, user) or user.role == Role.DATA_OPS):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the case owner or data ops can reverse a decision")
    if row.status != "decided":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a decided case can be reversed")
    now = clock.now()
    await session.execute(text("UPDATE users SET suspended_at = NULL WHERE id = :w"), {"w": row.subject_id})
    await session.execute(text("UPDATE holds SET released_at = :now WHERE case_id = :c AND released_at IS NULL"), {"now": now, "c": case_id})
    await add_action(session, case_id, "reverse", user.id, body.reason, now)
    record(session, actor=user, action="case.reversed", entity_type="case", entity_id=case_id, org_id=row.vendor_id,
           after={"reason": body.reason, "worker_id": str(row.subject_id)})
    await session.commit()
    return await case_detail(case_id, user, session)
