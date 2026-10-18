"""Onboarding routes (VM1, VM3, N14, D46): invites, the checklist, and data ops' overview."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, onboarding
from app.audit import record, snapshot
from app.auth import current_user, issue_token
from app.config import settings
from app.db import get_session
from app.models import Invite, Org, OrgKind, Role, User
from app.routes.auth import Token, UserOut
from app.scoping import require_roles, visible_org

router = APIRouter()

INVITE_DAYS = 7


class InviteIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


class InviteOut(BaseModel):
    org_id: uuid.UUID
    display_name: str
    # Shown once: data ops passes it on (the platform doesn't send email).
    code: str
    expires_at: datetime


class AcceptIn(BaseModel):
    code: str = Field(min_length=10, max_length=200)


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@router.post("/orgs/{org_id}/invites", status_code=status.HTTP_201_CREATED)
async def invite_admin(
    org_id: uuid.UUID, body: InviteIn, user: User = Depends(require_roles(Role.DATA_OPS)), session: AsyncSession = Depends(get_session)
) -> InviteOut:
    """Data ops invites a vendor's admin with a single-use link that expires in seven days."""
    org = await visible_org(org_id, user, session)
    if org.kind != OrgKind.VENDOR:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invites are for vendor workspaces")
    code = secrets.token_urlsafe(24)
    now = clock.now()
    invite = Invite(org_id=org.id, role=Role.VENDOR_ADMIN, display_name=body.display_name, code_hash=code_hash(code),
                    created_by=user.id, created_at=now, expires_at=now + timedelta(days=INVITE_DAYS))
    session.add(invite)
    await session.flush()
    record(session, actor=user, action="invite.created", entity_type="invite", entity_id=invite.id, org_id=org.id,
           after={"role": invite.role, "display_name": invite.display_name, "expires_at": invite.expires_at.isoformat()})
    await session.commit()
    return InviteOut(org_id=org.id, display_name=invite.display_name, code=code, expires_at=invite.expires_at)


@router.post("/auth/invites/accept")
async def accept_invite(body: AcceptIn, session: AsyncSession = Depends(get_session)) -> Token:
    """Opening the link creates the person's account and signs them in. Works once."""
    invite = await session.scalar(select(Invite).where(Invite.code_hash == code_hash(body.code)).with_for_update())
    if invite is None or invite.accepted_at is not None or invite.expires_at <= clock.now():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This invite link is used, expired or wrong; ask data ops for a new one")
    member = User(org_id=invite.org_id, role=invite.role, display_name=invite.display_name)
    session.add(member)
    await session.flush()
    invite.accepted_at, invite.user_id = clock.now(), member.id
    record(session, actor=member, action="user.added", entity_type="user", entity_id=member.id, org_id=invite.org_id,
           after={**snapshot(member), "invite_id": str(invite.id)})
    await session.commit()
    member.org = await session.get(Org, invite.org_id)
    return Token(access_token=issue_token(member), user=UserOut.of(member))


@router.get("/orgs/{org_id}/onboarding")
async def vendor_checklist(org_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """The vendor's checklist. A vendor's CI can poll this with a read token and wait for it to complete."""
    org = await visible_org(org_id, user, session)
    if org.kind != OrgKind.VENDOR or user.role not in (Role.VENDOR_ADMIN, Role.DATA_OPS, Role.ENVIRONMENT_BUILDER):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return await onboarding.checklist(session, org)


@router.get("/onboarding")
async def overview(user: User = Depends(require_roles(Role.DATA_OPS)), session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Every vendor that came in through onboarding, with its checklist and how long it took (N14)."""
    joined = select(Invite.org_id)
    vendors = await session.scalars(select(Org).where(Org.kind == OrgKind.VENDOR, (Org.onboarded_at.is_(None)) | Org.id.in_(joined)).order_by(Org.created_at.desc()))
    return [await onboarding.checklist(session, org) for org in vendors.all()]


@router.get("/tools/rlp.py")
async def vendor_cli() -> FileResponse:
    """The vendor command-line client, for a vendor's terminal or CI. Public: it holds no secrets."""
    return FileResponse(Path(settings.tools_dir) / "rlp.py", media_type="text/x-python", filename="rlp.py")
