import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select, update

from app import clock
from app.config import settings
from app.db import get_session
from app.models import ApiToken, Org, Role, TokenScope, User

bearer = HTTPBearer(auto_error=False)
# Clocks drift and get corrected in steps (WSL2 steps its clock back by most of a second), so a
# token checked moments after it was issued can look as if it came from the future.
CLOCK_SKEW_SECONDS = 10


def issue_token(user: User) -> str:
    now = datetime.now(UTC)
    claims = {"sub": str(user.id), "iat": now, "exp": now + timedelta(hours=settings.jwt_ttl_hours)}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


# Vendor API tokens carry a versioned prefix (D45), so the format can change without ambiguity.
TOKEN_PREFIX = "rlp1_"

# Gateway rules for API tokens (D45): the only routes a token reaches, and the role it acts in
# there. Anything not listed is refused, so a token can never answer tasks, review answers,
# decide cases, invite people or manage tokens. Read tokens get GETs only.
_ENVIRONMENTS = [
    ("POST", "/environments"), ("POST", "/environments/{environment_id}/versions"), ("POST", "/versions/{version_id}/submit"),
]
_READ = [
    ("GET", "/projects"), ("GET", "/projects/{project_id}"), ("GET", "/projects/{project_id}/environment-progress"),
    ("GET", "/projects/{project_id}/my-allocation"), ("GET", "/environments"), ("GET", "/environments/{environment_id}/versions"),
    ("GET", "/versions"), ("GET", "/versions/{version_id}"), ("GET", "/versions/{version_id}/checks"),
    ("GET", "/versions/{version_id}/diff"), ("GET", "/versions/{version_id}/reviews"),
    ("GET", "/versions/{version_id}/files/{path:path}"),
]
_VENDOR_READ = [
    ("GET", "/vendors/{vendor_id}/scorecard"), ("GET", "/vendors/{vendor_id}/workers"), ("GET", "/vendors/environment-work"),
    ("GET", "/cases"), ("GET", "/cases/{case_id}"),
]
TOKEN_ROUTES: dict[tuple[str, str], tuple[set[str], Role]] = {
    **{r: ({TokenScope.ENVIRONMENTS}, Role.ENVIRONMENT_BUILDER) for r in _ENVIRONMENTS},
    # A builder's tooling needs to see its own briefs and versions to submit against them.
    **{r: ({TokenScope.ENVIRONMENTS, TokenScope.READ}, Role.ENVIRONMENT_BUILDER) for r in _READ},
    **{r: ({TokenScope.READ}, Role.VENDOR_ADMIN) for r in _VENDOR_READ},
}


@dataclass
class TokenPrincipal:
    """Stands where a signed-in user would, but acts for the vendor, never for a person (D45)."""

    token_id: uuid.UUID
    org: Org
    role: Role
    display_name: str
    id: None = None
    suspended_at: None = None

    @property
    def org_id(self) -> uuid.UUID:
        return self.org.id

    @property
    def handle(self) -> str:
        return f"token:{self.display_name}"


def hash_secret(secret: str) -> str:
    # Secrets are 256 random bits, so a fast hash is enough; there's nothing to brute-force.
    return hashlib.sha256(secret.encode()).hexdigest()


async def active_token(secret: str, session: AsyncSession) -> ApiToken:
    """The live token with this secret, its last use recorded; 401 if it's unknown, revoked or expired."""
    token = await session.scalar(select(ApiToken).where(ApiToken.secret_hash == hash_secret(secret)))
    now = clock.now()
    if token is None or token.revoked_at is not None or token.expires_at <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token not recognized, revoked or expired")
    # Recorded at most once a minute, so reads don't each cost a write.
    if token.last_used_at is None or now - token.last_used_at > timedelta(minutes=1):
        await session.execute(update(ApiToken).where(ApiToken.id == token.id).values(last_used_at=now))
        await session.commit()
    return token


async def token_principal(secret: str, request: Request, session: AsyncSession) -> TokenPrincipal:
    token = await active_token(secret, session)
    route = request.scope.get("route")
    rule = TOKEN_ROUTES.get((request.method, getattr(route, "path", "")))
    if rule is None or not rule[0] & set(token.scopes):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API tokens can't do this, or this token lacks the scope")
    return TokenPrincipal(token_id=token.id, org=await session.get(Org, token.org_id), role=rule[1], display_name=token.name)


async def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    if credentials is None:
        raise unauthorized
    if credentials.credentials.startswith(TOKEN_PREFIX):
        return await token_principal(credentials.credentials, request, session)  # type: ignore[return-value]
    try:
        claims = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"], leeway=CLOCK_SKEW_SECONDS)
        user_id = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    user = await session.scalar(select(User).options(selectinload(User.org)).where(User.id == user_id))
    if user is None:
        raise unauthorized
    return user
