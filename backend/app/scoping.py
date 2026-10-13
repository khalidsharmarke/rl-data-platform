"""Workspace scoping: who may see and change what (VM1, N9).

Stands in for the API gateway's scoping in the build. Every route that touches an
organization's data goes through these checks. Anything outside the caller's reach is
reported as not found, so vendors can't learn what exists in other workspaces.
"""

import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import current_user
from app.models import Org, OrgKind, Role, User


def is_internal(user: User) -> bool:
    return user.org.kind == OrgKind.INTERNAL


def can_see_org(user: User, org_id: uuid.UUID) -> bool:
    return is_internal(user) or user.org_id == org_id


async def visible_org(org_id: uuid.UUID, user: User, session: AsyncSession) -> Org:
    org = await session.get(Org, org_id)
    if org is None or not can_see_org(user, org.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return org


def require_roles(*roles: Role):
    async def check(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role can't do this")
        return user

    return check


def shows_identity(viewer: User, member: User) -> bool:
    """Workers are pseudonymous outside their own vendor (D12, PRD PD1)."""
    return member.org_id == viewer.org_id or member.role != Role.WORKER

