"""Audit events (VM4). Write one in the same transaction as the change it records."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, Org, User


def snapshot(entity: Org | User) -> dict[str, Any]:
    if isinstance(entity, Org):
        return {"name": entity.name, "kind": entity.kind, "capabilities": list(entity.capabilities), "domains": list(entity.domains)}
    return {"org_id": str(entity.org_id), "role": entity.role, "display_name": entity.display_name, "handle": entity.handle}


def record(
    session: AsyncSession,
    *,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | str,
    org_id: uuid.UUID | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            org_id=org_id,
            before=before,
            after=after,
        )
    )
