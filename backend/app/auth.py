import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.config import settings
from app.db import get_session
from app.models import User

bearer = HTTPBearer(auto_error=False)


def issue_token(user: User) -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(user.id), "iat": now, "exp": now + timedelta(hours=settings.jwt_ttl_hours)}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    if credentials is None:
        raise unauthorized
    try:
        claims = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
        user_id = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    user = await session.scalar(select(User).options(selectinload(User.org)).where(User.id == user_id))
    if user is None:
        raise unauthorized
    return user
