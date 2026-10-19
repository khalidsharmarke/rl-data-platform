"""The match key, workers' identity hashes (AB10, D55), and vendor-private rules (AB9, D56)."""

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.audit import record
from app.config import settings
from app.db import get_session
from app.models import Role, User
from app.scoping import require_roles
from app.vendor_rules import SIGNALS

router = APIRouter()


class MatchKey(BaseModel):
    key: str
    version: int
    how: str


@router.get("/match-key")
async def match_key(user: User = Depends(require_roles(Role.VENDOR_ADMIN))) -> MatchKey:
    """The key every vendor hashes its workers' identifiers with, so the same person matches across vendors."""
    return MatchKey(key=settings.match_key, version=settings.match_key_version,
                    how="HMAC-SHA256 with this key of the worker's email address, trimmed and lowercased; send it as 64 hex digits")


class IdentityIn(BaseModel):
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.put("/orgs/{org_id}/members/{worker_id}/identity", status_code=status.HTTP_204_NO_CONTENT)
async def set_identity(org_id: uuid.UUID, worker_id: uuid.UUID, body: IdentityIn, user: User = Depends(require_roles(Role.VENDOR_ADMIN)),
                       session: AsyncSession = Depends(get_session)) -> None:
    """A vendor records the keyed hash of one of its workers. The platform never sees the identifier."""
    worker = await session.get(User, worker_id)
    if org_id != user.org_id or worker is None or worker.org_id != org_id or worker.role != Role.WORKER:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such worker in your workspace")
    await session.execute(text(
        "INSERT INTO worker_identities (worker_id, hash, key_version, submitted_at) VALUES (:w, :h, :k, :now) "
        "ON CONFLICT (worker_id) DO UPDATE SET hash = excluded.hash, key_version = excluded.key_version, submitted_at = excluded.submitted_at"),
        {"w": worker.id, "h": body.hash, "k": settings.match_key_version, "now": clock.now()})
    # The hash itself stays out of the audit log, which more people read.
    record(session, actor=user, action="worker.identity_set", entity_type="user", entity_id=worker.id, org_id=org_id,
           after={"key_version": settings.match_key_version})
    await session.commit()


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    signal: str = Field(pattern="^(" + "|".join(SIGNALS) + ")$")
    threshold: Decimal = Field(ge=0, le=100_000)
    window: int = Field(default=20, ge=5, le=500)


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    signal: str
    fires: str  # "above" or "below" the threshold
    threshold: float
    window: int
    created_at: datetime
    retired_at: datetime | None


def rule_out(r) -> RuleOut:
    return RuleOut(id=r.id, name=r.name, signal=r.signal, fires=SIGNALS[r.signal][1], threshold=float(r.threshold), window=r.window,
                   created_at=r.created_at, retired_at=r.retired_at)


@router.get("/vendor-rules")
async def list_rules(user: User = Depends(require_roles(Role.VENDOR_ADMIN)), session: AsyncSession = Depends(get_session)) -> list[RuleOut]:
    """Your vendor's own rules. Nobody else sees them."""
    rows = await session.execute(text("SELECT * FROM vendor_rules WHERE org_id = :o ORDER BY created_at"), {"o": user.org_id})
    return [rule_out(r) for r in rows]


@router.post("/vendor-rules", status_code=status.HTTP_201_CREATED)
async def create_rule(body: RuleIn, user: User = Depends(require_roles(Role.VENDOR_ADMIN)), session: AsyncSession = Depends(get_session)) -> RuleOut:
    r = (await session.execute(text(
        "INSERT INTO vendor_rules (org_id, name, signal, threshold, \"window\", created_by, created_at) "
        "VALUES (:o, :n, :s, :t, :w, :u, :now) RETURNING *"),
        {"o": user.org_id, "n": body.name, "s": body.signal, "t": body.threshold, "w": body.window, "u": user.id, "now": clock.now()})).one()
    record(session, actor=user, action="vendor_rule.created", entity_type="vendor_rule", entity_id=r.id, org_id=user.org_id,
           after={"name": r.name, "signal": r.signal, "threshold": str(r.threshold), "window": r.window})
    await session.commit()
    return rule_out(r)


@router.post("/vendor-rules/{rule_id}/retire")
async def retire_rule(rule_id: uuid.UUID, user: User = Depends(require_roles(Role.VENDOR_ADMIN)), session: AsyncSession = Depends(get_session)) -> RuleOut:
    r = (await session.execute(text("UPDATE vendor_rules SET retired_at = coalesce(retired_at, :now) WHERE id = :i AND org_id = :o RETURNING *"),
                               {"now": clock.now(), "i": rule_id, "o": user.org_id})).first()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such rule")
    record(session, actor=user, action="vendor_rule.retired", entity_type="vendor_rule", entity_id=r.id, org_id=user.org_id)
    await session.commit()
    return rule_out(r)
