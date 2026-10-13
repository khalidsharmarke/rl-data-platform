import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import current_user, issue_token
from app.config import settings
from app.db import get_session
from app.models import Org, User

router = APIRouter(prefix="/auth")


class UserOut(BaseModel):
    id: uuid.UUID
    display_name: str
    handle: str
    role: str
    org_id: uuid.UUID
    org_name: str
    org_kind: str

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            display_name=user.display_name,
            handle=user.handle,
            role=user.role,
            org_id=user.org_id,
            org_name=user.org.name,
            org_kind=user.org.kind,
        )


class DemoLogin(BaseModel):
    user_id: uuid.UUID


class Token(BaseModel):
    access_token: str
    user: UserOut


def require_demo_logins() -> None:
    if not settings.demo_logins:
        raise HTTPException(status.HTTP_404_NOT_FOUND)


@router.get("/demo-users", dependencies=[Depends(require_demo_logins)])
async def demo_users(session: AsyncSession = Depends(get_session)) -> list[UserOut]:
    users = await session.scalars(
        select(User).join(Org).options(selectinload(User.org)).order_by(Org.kind, Org.name, User.role, User.display_name)
    )
    return [UserOut.of(u) for u in users]


@router.post("/demo-login", dependencies=[Depends(require_demo_logins)])
async def demo_login(body: DemoLogin, session: AsyncSession = Depends(get_session)) -> Token:
    user = await session.scalar(select(User).options(selectinload(User.org)).where(User.id == body.user_id))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    return Token(access_token=issue_token(user), user=UserOut.of(user))


@router.get("/me")
async def me(user: User = Depends(current_user)) -> UserOut:
    return UserOut.of(user)
