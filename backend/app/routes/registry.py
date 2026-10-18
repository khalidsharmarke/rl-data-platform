"""The registry's token endpoint (VM2, D45, D50): `docker login` with a vendor API token.

The registry sends clients here (Docker's token authentication). A client presents a vendor
API token with the registry scope as its password, and gets a short-lived registry token for
what it asked, cut down to the vendor's own namespace. Revoking the API token ends access
within the registry token's five minutes.
"""

import base64
import binascii
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import registry
from app.auth import active_token
from app.config import settings
from app.db import get_session
from app.models import Org, TokenScope

router = APIRouter()


def basic_password(request: Request) -> tuple[str, str]:
    header = request.headers.get("Authorization", "")
    try:
        user, _, password = base64.b64decode(header.removeprefix("Basic "), validate=True).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        user, password = "", ""
    if not header.startswith("Basic ") or not password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in with an API token as the password",
                            headers={"WWW-Authenticate": 'Basic realm="rl-registry"'})
    return user, password


@router.get("/registry/token")
async def registry_token(request: Request, scope: str = "", session: AsyncSession = Depends(get_session)) -> dict:
    if not settings.registry_url:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    user, password = basic_password(request)
    if user == "platform" and settings.registry_platform_secret and hmac.compare_digest(password, settings.registry_platform_secret):
        access = [{"type": "repository", "name": n, "actions": a} for n, a in registry.requested(scope)]
        subject = "platform"
    else:
        token = await active_token(password, session)
        if TokenScope.REGISTRY not in token.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This token lacks the registry scope")
        org = await session.get(Org, token.org_id)
        access, subject = registry.vendor_grants(org.name, scope), f"token:{token.id}"
    signed = registry.mint(subject, access)
    return {"token": signed, "access_token": signed, "expires_in": registry.TOKEN_SECONDS}
