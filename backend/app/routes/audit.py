import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AuditEvent, Role, User
from app.scoping import is_internal, require_roles

router = APIRouter(prefix="/audit")


class AuditOut(BaseModel):
    id: int
    at: datetime
    actor_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str
    org_id: uuid.UUID | None
    token_id: uuid.UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None


@router.get("")
async def list_events(
    limit: int = Query(100, ge=1, le=500),
    token_id: uuid.UUID | None = None,
    user: User = Depends(require_roles(Role.DATA_OPS, Role.VENDOR_ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> list[AuditOut]:
    query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if not is_internal(user):
        query = query.where(AuditEvent.org_id == user.org_id)
    # Everything one token did, for a leak investigation.
    if token_id:
        query = query.where(AuditEvent.token_id == token_id)
    return [AuditOut.model_validate(e, from_attributes=True) for e in await session.scalars(query)]
