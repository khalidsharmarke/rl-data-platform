"""Vendor API tokens (VM2, D45): the vendor org's credentials for its own tooling."""

import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.audit import record
from app.auth import TOKEN_PREFIX, hash_secret
from app.db import get_session
from app.models import ApiToken, Org, Role, TokenScope, User
from app.scoping import is_internal, require_roles

router = APIRouter(prefix="/tokens")

TOKEN_DAYS = 90


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[TokenScope] = Field(min_length=1)


class TokenOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    org_name: str
    name: str
    scopes: list[str]
    prefix: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class NewToken(TokenOut):
    # Shown once, at creation; only its hash is stored.
    secret: str


def token_out(token: ApiToken, org: Org) -> TokenOut:
    return TokenOut(id=token.id, org_id=org.id, org_name=org.name, name=token.name, scopes=token.scopes, prefix=token.prefix,
                    created_at=token.created_at, expires_at=token.expires_at, last_used_at=token.last_used_at,
                    revoked_at=token.revoked_at)


@router.get("")
async def list_tokens(
    user: User = Depends(require_roles(Role.VENDOR_ADMIN, Role.DATA_OPS)), session: AsyncSession = Depends(get_session)
) -> list[TokenOut]:
    query = select(ApiToken, Org).join(Org, Org.id == ApiToken.org_id).order_by(Org.name, ApiToken.created_at.desc())
    if not is_internal(user):
        query = query.where(ApiToken.org_id == user.org_id)
    return [token_out(t, o) for t, o in (await session.execute(query)).all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_token(
    body: TokenIn, user: User = Depends(require_roles(Role.VENDOR_ADMIN)), session: AsyncSession = Depends(get_session)
) -> NewToken:
    """Rotation is creating a new token, moving tooling to it, then revoking the old one."""
    if await session.scalar(select(ApiToken.id).where(
            ApiToken.org_id == user.org_id, ApiToken.name == body.name, ApiToken.revoked_at.is_(None))):
        raise HTTPException(status.HTTP_409_CONFLICT, "An active token already has that name")
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = clock.now()
    token = ApiToken(org_id=user.org_id, name=body.name, scopes=sorted(set(body.scopes)), prefix=secret[:12],
                     secret_hash=hash_secret(secret), created_by=user.id, created_at=now, expires_at=now + timedelta(days=TOKEN_DAYS))
    session.add(token)
    await session.flush()
    record(session, actor=user, action="token.created", entity_type="api_token", entity_id=token.id, org_id=user.org_id,
           after={"name": token.name, "scopes": token.scopes, "prefix": token.prefix, "expires_at": token.expires_at.isoformat()})
    await session.commit()
    return NewToken(**token_out(token, user.org).model_dump(), secret=secret)


@router.post("/{token_id}/revoke")
async def revoke_token(
    token_id: uuid.UUID, user: User = Depends(require_roles(Role.VENDOR_ADMIN, Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> TokenOut:
    """The vendor's admin, or data ops responding to a leak, can revoke a token."""
    token = await session.get(ApiToken, token_id)
    if token is None or not (is_internal(user) or token.org_id == user.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if token.revoked_at is None:
        token.revoked_at, token.revoked_by = clock.now(), user.id
        record(session, actor=user, action="token.revoked", entity_type="api_token", entity_id=token.id, org_id=token.org_id,
               after={"name": token.name, "prefix": token.prefix})
        await session.commit()
    return token_out(token, await session.get(Org, token.org_id))
