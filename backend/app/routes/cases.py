import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, environments, identity
from app.abuse import add_action
from app.environment_cases import flag_version
from app.audit import record
from app.db import get_session
from app.models import Allocation, Environment, EnvironmentVersion, Project, Role, User
from app.scoping import is_internal, require_roles
from app.vendor_oversight import RESPONSE_WINDOW
from app.work import route_tasks

router = APIRouter(prefix="/cases")

DECISIONS = {"clear", "warn", "suspend", "remove"}
# An environment case (TO6, D13): clear sends the version back to review; remove keeps it
# quarantined for good, out of the catalog.
ENVIRONMENT_DECISIONS = {"clear", "remove"}
# A vendor case ends cleared (nothing to act on) or resolved, after at least one action (D42).
VENDOR_DECISIONS = {"clear", "resolve"}
VENDOR_ACTIONS = {"lower_allocation", "pause", "offboard"}


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
    by_person: bool  # every flag was raised by a person, not a detector
    shared_at: datetime | None  # vendor cases: when data ops asked the vendor to respond
    private: bool = False  # opened by the vendor's own rule; Anthropic never sees it (D56)


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
    # Everything the browser reported (AB11): untrusted, shown as evidence beside what the server measured.
    client_signals: dict[str, Any]


class ApiSignals(BaseModel):
    """What the server observed about a worker (AB12): the same measurement for everyone."""
    claims_last_hour: int
    most_claims_in_a_minute: int
    sessions_last_day: int
    devices_last_day: int
    other_workers_on_those_devices: int


class Action(BaseModel):
    action: str
    by: str
    note: str | None
    at: datetime


class Pull(BaseModel):
    puller: str
    run_label: str | None
    pulled_at: datetime


class EnvironmentSubject(BaseModel):
    version_id: uuid.UUID
    environment_id: uuid.UUID
    name: str
    number: int
    status: str
    pulls: list[Pull]


class CaseDetail(CaseSummary):
    subject_id: uuid.UUID
    project_id: uuid.UUID | None
    decision_reason: str | None
    flags: list[Flag]
    samples: list[Sample]
    actions: list[Action]
    environment: EnvironmentSubject | None = None
    respond_by: datetime | None = None  # vendor cases: actions are allowed after a response, or after this
    can_act: bool = False
    api_signals: ApiSignals | None = None  # worker cases
    matches: list[dict] = []  # Anthropic only: cross-vendor matches behind the case (AB10, D55)


class EnvironmentCaseIn(BaseModel):
    version_id: uuid.UUID
    observation: str = Field(min_length=3, max_length=1000)


class DecisionIn(BaseModel):
    decision: str
    reason: str = Field(min_length=3, max_length=1000)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class ResponseIn(BaseModel):
    text: str = Field(min_length=3, max_length=4000)


class VendorActionIn(BaseModel):
    action: str
    reason: str = Field(min_length=3, max_length=1000)
    project_id: uuid.UUID | None = None  # lower_allocation: on which project; pause: one project, or every one if None
    share: Decimal | None = Field(default=None, gt=0, lt=1)


CASES = """
SELECT c.*, o.name AS vendor_name, u.handle, u.display_name, u.org_id AS subject_org,
       ev.number AS env_number, e.name AS env_name, ev.status AS env_status, e.id AS env_id,
       (EXISTS (SELECT 1 FROM holds h WHERE h.case_id = c.id AND h.released_at IS NULL)
        OR coalesce(ev.status = 'quarantined', false)) AS held,
       coalesce((SELECT array_agg(f.observation ORDER BY f.created_at) FROM flags f WHERE f.case_id = c.id), '{}') AS observations,
       coalesce((SELECT bool_and(f.rule = 'person') FROM flags f WHERE f.case_id = c.id), true) AS by_person
FROM cases c
JOIN orgs o ON o.id = c.vendor_id
LEFT JOIN users u ON u.id = c.subject_id AND c.subject_type = 'worker'
LEFT JOIN environment_versions ev ON ev.id = c.subject_id AND c.subject_type = 'environment_version'
LEFT JOIN environments e ON e.id = ev.environment_id
"""


def summary(row, viewer: User) -> dict:
    # Workers are pseudonymous outside their vendor (D12).
    name = row.display_name if row.subject_org == viewer.org_id else None
    return dict(
        id=row.id, vendor_name=row.vendor_name, subject_type=row.subject_type,
        subject_handle=(f"{row.env_name} v{row.env_number}" if row.subject_type == "environment_version"
                        else row.vendor_name if row.subject_type == "vendor" else (row.handle or "—")),
        subject_name=name, owner=row.owner, status=row.status, opened_at=row.opened_at, decision=row.decision,
        decided_at=row.decided_at, held=row.held, observations=list(row.observations), by_person=row.by_person,
        shared_at=row.shared_at, private=row.private,
    )


# A vendor case is Anthropic's until data ops shares it with the vendor (D42).
VENDOR_SEES = "(c.subject_type <> 'vendor' OR c.shared_at IS NOT NULL)"


async def visible_case(session: AsyncSession, case_id: uuid.UUID, user: User):
    row = (await session.execute(text(CASES + " WHERE c.id = :id"), {"id": case_id})).first()
    vendor_may = row is not None and row.vendor_id == user.org_id and (row.subject_type != "vendor" or row.shared_at is not None)
    if row is None or not ((is_internal(user) and not row.private) or vendor_may):
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
    if is_internal(user):
        where.append("NOT c.private")
    else:
        where.append("c.vendor_id = :v AND " + VENDOR_SEES)
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
        Sample(submission_id=s.id, prompt=s.prompt or "", answer=s.answer, time_on_task_s=round(s.time_on_task_ms / 1000, 1),
               pasted=bool(s.pasted), client_signals=s.client_signals or {})
        for s in await session.execute(
            text(
                "SELECT s.id, t.payload->>'prompt' AS prompt, s.answer, s.time_on_task_ms, (s.client_signals->>'pasted')::boolean AS pasted, "
                "s.client_signals "
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
    environment = None
    if row.subject_type == "environment_version":
        pulls = await session.execute(text("SELECT puller, run_label, pulled_at FROM catalog_pulls WHERE version_id = :v ORDER BY id"),
                                      {"v": row.subject_id})
        environment = EnvironmentSubject(version_id=row.subject_id, environment_id=row.env_id, name=row.env_name, number=row.env_number,
                                         status=row.env_status, pulls=[Pull(**p._mapping) for p in pulls])
    respond_by = row.shared_at + RESPONSE_WINDOW if row.subject_type == "vendor" and row.shared_at else None
    return CaseDetail(**summary(row, user), subject_id=row.subject_id, project_id=row.project_id, decision_reason=row.decision_reason,
                      flags=flags, samples=samples, actions=actions, environment=environment, respond_by=respond_by,
                      can_act=user.role == Role.DATA_OPS and row.status == "open" and await may_act(session, row),
                      api_signals=await api_signals(session, row.subject_id) if row.subject_type == "worker" else None,
                      matches=await case_matches(session, row, flags) if internal else [])


async def case_matches(session: AsyncSession, row, flags: list[Flag]) -> list[dict]:
    """The workers behind a case who match a worker at another vendor, for data ops only (D55)."""
    workers = [row.subject_id] if row.subject_type == "worker" else [
        uuid.UUID(w) for f in flags for w in f.evidence.get("matched_worker_ids", [])]
    return [m for w in workers for m in await identity.matches(session, w)]


API_SIGNALS = text("""
WITH mine AS (SELECT * FROM assignments WHERE worker_id = :w AND assigned_at > :day)
SELECT (SELECT count(*) FROM mine WHERE assigned_at > :hour) AS claims_last_hour,
       (SELECT coalesce(max(n), 0) FROM (SELECT count(*) AS n FROM mine GROUP BY date_trunc('minute', assigned_at)) m) AS most_claims_in_a_minute,
       (SELECT count(DISTINCT session_id) FROM mine) AS sessions_last_day,
       (SELECT count(DISTINCT device_id) FROM mine) AS devices_last_day,
       (SELECT count(DISTINCT a.worker_id) FROM assignments a
         WHERE a.device_id IN (SELECT device_id FROM mine WHERE device_id IS NOT NULL) AND a.worker_id <> :w
           AND a.assigned_at > :day) AS other_workers_on_those_devices
""")


async def api_signals(session: AsyncSession, worker_id: uuid.UUID) -> ApiSignals:
    now = clock.now()
    r = (await session.execute(API_SIGNALS, {"w": worker_id, "day": now - timedelta(days=1), "hour": now - timedelta(hours=1)})).one()
    return ApiSignals(**r._mapping)


async def may_act(session: AsyncSession, row) -> bool:
    """Actions on a vendor wait for its response, or for the response window to pass (AB5, D42)."""
    if row.subject_type != "vendor" or row.shared_at is None:
        return False
    responded = await session.scalar(text("SELECT 1 FROM case_actions WHERE case_id = :c AND action = 'response' LIMIT 1"),
                                     {"c": row.id})
    return bool(responded) or clock.now() >= row.shared_at + RESPONSE_WINDOW


@router.post("/environment", status_code=status.HTTP_201_CREATED)
async def open_environment_case(
    body: EnvironmentCaseIn, user: User = Depends(require_roles(Role.DATA_OPS, Role.RESEARCHER)), session: AsyncSession = Depends(get_session)
) -> CaseDetail:
    """A person at Anthropic flags an approved environment version (AB3, TO6, D13, D39), from a
    review, a dashboard or an episode. The case is data ops' to
    decide, and the version is quarantined at once: out of the catalog, reversibly (AB8, D16)."""
    version = await session.get(EnvironmentVersion, body.version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if version.status != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an approved version can be quarantined")
    case_id = await flag_version(session, version, observation=body.observation, actor=user,
                                 evidence={"flagged_by": str(user.id)})
    await session.commit()
    return await case_detail(case_id, user, session)


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
    if row.subject_type == "environment_version":
        return await decide_environment(row, body, user, session)
    if row.subject_type == "vendor":
        return await decide_vendor(row, body, user, session)
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


async def decide_environment(row, body: DecisionIn, user: User, session: AsyncSession) -> CaseDetail:
    if body.decision not in ENVIRONMENT_DECISIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Decision must be one of {sorted(ENVIRONMENT_DECISIONS)}")
    now = clock.now()
    await session.execute(
        text("UPDATE cases SET status = 'decided', decision = :d, decision_reason = :r, decided_by = :u, decided_at = :now WHERE id = :c"),
        {"d": body.decision, "r": body.reason, "u": user.id, "now": now, "c": row.id},
    )
    await add_action(session, row.id, body.decision, user.id, body.reason, now)
    if body.decision == "clear":
        version = await session.get(EnvironmentVersion, row.subject_id)
        await environments.transition(session, version, "in_review", actor=user, note=body.reason)
        await add_action(session, row.id, "release", user.id, "Version sent back to review", now)
    record(session, actor=user, action=f"case.{body.decision}", entity_type="case", entity_id=row.id, org_id=row.vendor_id,
           after={"decision": body.decision, "reason": body.reason, "version_id": str(row.subject_id)})
    await session.commit()
    return await case_detail(row.id, user, session)


async def decide_vendor(row, body: DecisionIn, user: User, session: AsyncSession) -> CaseDetail:
    if body.decision not in VENDOR_DECISIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Decision must be one of {sorted(VENDOR_DECISIONS)}")
    acted = await session.scalar(text("SELECT 1 FROM case_actions WHERE case_id = :c AND action = ANY(:a) LIMIT 1"),
                                 {"c": row.id, "a": sorted(VENDOR_ACTIONS)})
    if body.decision == "resolve" and not acted:
        raise HTTPException(status.HTTP_409_CONFLICT, "Take an action on the vendor first, or clear the case")
    now = clock.now()
    await session.execute(
        text("UPDATE cases SET status = 'decided', decision = :d, decision_reason = :r, decided_by = :u, decided_at = :now WHERE id = :c"),
        {"d": body.decision, "r": body.reason, "u": user.id, "now": now, "c": row.id},
    )
    await add_action(session, row.id, body.decision, user.id, body.reason, now)
    record(session, actor=user, action=f"case.{body.decision}", entity_type="case", entity_id=row.id,
           org_id=row.vendor_id if row.shared_at else None, after={"decision": body.decision, "reason": body.reason})
    await session.commit()
    return await case_detail(row.id, user, session)


@router.post("/{case_id}/share")
async def share(case_id: uuid.UUID, user: User = Depends(require_roles(Role.DATA_OPS)),
                session: AsyncSession = Depends(get_session)) -> CaseDetail:
    """Data ops has read the vendor finding and asks the vendor to respond; the window starts now."""
    row = await visible_case(session, case_id, user)
    if row.subject_type != "vendor" or row.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an open vendor case is shared with the vendor")
    if row.shared_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already shared")
    now = clock.now()
    await session.execute(text("UPDATE cases SET shared_at = :now WHERE id = :c"), {"now": now, "c": case_id})
    await add_action(session, case_id, "shared", user.id,
                     f"{row.vendor_name} asked to respond by {(now + RESPONSE_WINDOW):%d %b %H:%M}", now)
    record(session, actor=user, action="case.shared", entity_type="case", entity_id=case_id, org_id=row.vendor_id,
           after={"respond_by": (now + RESPONSE_WINDOW).isoformat()})
    await session.commit()
    return await case_detail(case_id, user, session)


@router.post("/{case_id}/responses", status_code=status.HTTP_201_CREATED)
async def respond(case_id: uuid.UUID, body: ResponseIn, user: User = Depends(require_roles(Role.VENDOR_ADMIN)),
                  session: AsyncSession = Depends(get_session)) -> CaseDetail:
    """The vendor's answer to a vendor case, kept on the case (AB5)."""
    row = await visible_case(session, case_id, user)
    if row.subject_type != "vendor" or row.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an open vendor case takes responses")
    now = clock.now()
    await add_action(session, case_id, "response", user.id, body.text, now)
    record(session, actor=user, action="case.response", entity_type="case", entity_id=case_id, org_id=row.vendor_id,
           after={"text": body.text})
    await session.commit()
    return await case_detail(case_id, user, session)


@router.post("/{case_id}/actions")
async def act_on_vendor(case_id: uuid.UUID, body: VendorActionIn, user: User = Depends(require_roles(Role.DATA_OPS)),
                        session: AsyncSession = Depends(get_session)) -> CaseDetail:
    """Lower an allocation, pause or offboard (AB5, D42). Routing follows at once."""
    row = await visible_case(session, case_id, user)
    if row.subject_type != "vendor" or row.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, "Actions are taken on an open vendor case")
    if body.action not in VENDOR_ACTIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Action must be one of {sorted(VENDOR_ACTIONS)}")
    if not await may_act(session, row):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The vendor hasn't responded and its response window hasn't passed" if row.shared_at
                            else "Ask the vendor to respond first")
    now = clock.now()
    if body.action == "lower_allocation":
        allocation = await session.scalar(
            select(Allocation).where(Allocation.project_id == body.project_id, Allocation.vendor_id == row.vendor_id,
                                     Allocation.status == "active").with_for_update())
        if allocation is None or body.share is None or body.share >= allocation.share:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Name a project the vendor works on, and a lower share")
        note = f"Share on the project lowered from {allocation.share:.0%} to {body.share:.0%}: {body.reason}"
        allocation.share = body.share
        affected = [allocation.project_id]
    else:
        new_status = "paused" if body.action == "pause" else "ended"
        only = body.project_id if body.action == "pause" else None
        changed = (await session.execute(
            text("UPDATE allocations SET status = :s WHERE vendor_id = :v AND status <> 'ended' "
                 "AND (CAST(:p AS uuid) IS NULL OR project_id = CAST(:p AS uuid)) RETURNING project_id"),
            {"s": new_status, "v": row.vendor_id, "p": only})).scalars().all()
        if not changed:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The vendor has no allocation to change there")
        if body.action == "offboard":
            await session.execute(text("UPDATE orgs SET offboarded_at = :now WHERE id = :v"), {"now": now, "v": row.vendor_id})
        note = f"{'Paused' if body.action == 'pause' else 'Offboarded'} on {len(changed)} project(s): {body.reason}"
        affected = changed
    await session.flush()
    for project_id in affected:
        await route_tasks(session, await session.get(Project, project_id))  # unstarted tasks move to other vendors
    await add_action(session, case_id, body.action, user.id, note, now)
    record(session, actor=user, action=f"vendor.{body.action}", entity_type="org", entity_id=row.vendor_id, org_id=row.vendor_id,
           after={"case_id": str(case_id), "reason": body.reason, "project_id": str(body.project_id) if body.project_id else None,
                  "share": str(body.share) if body.share is not None else None})
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
    if row.subject_type == "environment_version":
        raise HTTPException(status.HTTP_409_CONFLICT, "An environment case is reversed by reviewing the version again")
    if row.subject_type == "vendor":
        raise HTTPException(status.HTTP_409_CONFLICT, "A vendor case is undone by changing the allocation back")
    now = clock.now()
    await session.execute(text("UPDATE users SET suspended_at = NULL WHERE id = :w"), {"w": row.subject_id})
    await session.execute(text("UPDATE holds SET released_at = :now WHERE case_id = :c AND released_at IS NULL"), {"now": now, "c": case_id})
    await add_action(session, case_id, "reverse", user.id, body.reason, now)
    record(session, actor=user, action="case.reversed", entity_type="case", entity_id=case_id, org_id=row.vendor_id,
           after={"reason": body.reason, "worker_id": str(row.subject_id)})
    await session.commit()
    return await case_detail(case_id, user, session)
