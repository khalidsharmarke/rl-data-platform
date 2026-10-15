"""Environments and their versions (EC0–EC2, EC8, D3, D33, D34).

An environment builder works only against a brief allocated to their vendor, and only if
their vendor supplies environments (VM6). Other vendors can't see a vendor's environments
(N9); Anthropic staff see all of them.
"""

import base64
import binascii
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, environments
from app.audit import record
from app.auth import current_user
from app.db import get_session
from app.models import Allocation, Capability, Environment, EnvironmentVersion, Org, Project, Role, User
from app.scoping import is_internal, require_roles

router = APIRouter()


class EnvironmentIn(BaseModel):
    project_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)


class EnvironmentOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    vendor_name: str
    name: str
    created_at: datetime
    versions: int
    latest_status: str | None


class FileIn(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    part: str
    content_b64: str


class VersionIn(BaseModel):
    test_command: str = Field(min_length=1, max_length=500)
    files: list[FileIn] = Field(min_length=1, max_length=200)


class VersionOut(BaseModel):
    id: uuid.UUID
    environment_id: uuid.UUID
    number: int
    status: str
    manifest: dict[str, Any]
    created_at: datetime
    status_changed_at: datetime


def version_out(v: EnvironmentVersion) -> VersionOut:
    return VersionOut.model_validate(v, from_attributes=True)


async def visible_environment(environment_id: uuid.UUID, user: User, session: AsyncSession) -> Environment:
    env = await session.get(Environment, environment_id)
    if env is None or not (is_internal(user) or env.org_id == user.org_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return env


async def visible_version(version_id: uuid.UUID, user: User, session: AsyncSession) -> tuple[EnvironmentVersion, Environment]:
    version = await session.get(EnvironmentVersion, version_id)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return version, await visible_environment(version.environment_id, user, session)


async def require_brief_for_builder(project_id: uuid.UUID, user: User, session: AsyncSession) -> Project:
    """The builder's vendor must supply environments and hold an active allocation of this brief."""
    org = await session.get(Org, user.org_id)
    if Capability.ENVIRONMENTS not in org.capabilities:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{org.name} isn't set up to supply environments")
    project = await session.get(Project, project_id)
    allocated = project is not None and await session.scalar(
        select(Allocation.id).where(Allocation.project_id == project.id, Allocation.vendor_id == org.id, Allocation.status == "active")
    )
    if not allocated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such brief allocated to your vendor")
    if project.kind != "environment":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That project is human-data work, not an environment brief")
    return project


@router.post("/environments", status_code=status.HTTP_201_CREATED)
async def create_environment(
    body: EnvironmentIn, user: User = Depends(require_roles(Role.ENVIRONMENT_BUILDER)), session: AsyncSession = Depends(get_session)
) -> EnvironmentOut:
    project = await require_brief_for_builder(body.project_id, user, session)
    if await session.scalar(select(Environment.id).where(
            Environment.project_id == project.id, Environment.org_id == user.org_id, Environment.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Your vendor already has an environment with that name for this brief")
    env = Environment(org_id=user.org_id, project_id=project.id, name=body.name, created_by=user.id, created_at=clock.now())
    session.add(env)
    await session.flush()
    record(session, actor=user, action="environment.created", entity_type="environment", entity_id=env.id, org_id=user.org_id,
           after={"project_id": str(project.id), "name": env.name})
    await session.commit()
    return (await environment_rows(session, user, env_id=env.id))[0]


async def environment_rows(session: AsyncSession, user: User, *, project_id: uuid.UUID | None = None,
                           env_id: uuid.UUID | None = None) -> list[EnvironmentOut]:
    query = select(Environment, Org.name).join(Org, Org.id == Environment.org_id).order_by(Environment.created_at)
    if not is_internal(user):
        query = query.where(Environment.org_id == user.org_id)
    if project_id:
        query = query.where(Environment.project_id == project_id)
    if env_id:
        query = query.where(Environment.id == env_id)
    out = []
    for env, vendor_name in (await session.execute(query)).all():
        versions = (await session.scalars(
            select(EnvironmentVersion).where(EnvironmentVersion.environment_id == env.id).order_by(EnvironmentVersion.number.desc())
        )).all()
        out.append(EnvironmentOut(id=env.id, project_id=env.project_id, vendor_name=vendor_name, name=env.name, created_at=env.created_at,
                                  versions=len(versions), latest_status=versions[0].status if versions else None))
    return out


@router.get("/environments")
async def list_environments(
    project_id: uuid.UUID | None = None, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[EnvironmentOut]:
    return await environment_rows(session, user, project_id=project_id)


@router.post("/environments/{environment_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(
    environment_id: uuid.UUID, body: VersionIn,
    user: User = Depends(require_roles(Role.ENVIRONMENT_BUILDER)), session: AsyncSession = Depends(get_session),
) -> VersionOut:
    """Every change is a new, immutable version (EC2, D3), its files stored by hash in three parts (D34)."""
    env = await visible_environment(environment_id, user, session)
    await require_brief_for_builder(env.project_id, user, session)
    files = []
    for f in body.files:
        try:
            files.append((f.path, f.part, base64.b64decode(f.content_b64, validate=True)))
        except (binascii.Error, ValueError):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{f.path}: content isn't valid base64")
    manifest = await environments.build_manifest(session, files, body.test_command)
    now = clock.now()
    version = EnvironmentVersion(environment_id=env.id, number=await environments.next_number(session, env.id), manifest=manifest,
                                 status="draft", created_by=user.id, created_at=now, status_changed_at=now)
    session.add(version)
    await session.flush()
    record(session, actor=user, action="environment_version.created", entity_type="environment_version", entity_id=version.id,
           org_id=env.org_id, after={"environment_id": str(env.id), "number": version.number, "files": len(manifest["files"])})
    await session.commit()
    return version_out(version)


@router.get("/environments/{environment_id}/versions")
async def list_versions(environment_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> list[VersionOut]:
    env = await visible_environment(environment_id, user, session)
    rows = await session.scalars(select(EnvironmentVersion).where(EnvironmentVersion.environment_id == env.id).order_by(EnvironmentVersion.number))
    return [version_out(v) for v in rows]


@router.get("/versions/{version_id}")
async def get_version(version_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> VersionOut:
    version, _ = await visible_version(version_id, user, session)
    return version_out(version)


@router.get("/versions/{version_id}/files/{path:path}")
async def get_file(version_id: uuid.UUID, path: str, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> Response:
    """A version's file, for its vendor and for Anthropic reviewers (N22). Training reads through the catalog."""
    version, _ = await visible_version(version_id, user, session)
    entry = next((f for f in version.manifest["files"] if f["path"] == path), None)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return Response(environments.read(entry["hash"]), media_type="application/octet-stream")


@router.post("/versions/{version_id}/submit")
async def submit_version(
    version_id: uuid.UUID, user: User = Depends(require_roles(Role.ENVIRONMENT_BUILDER)), session: AsyncSession = Depends(get_session)
) -> VersionOut:
    version, env = await visible_version(version_id, user, session)
    await require_brief_for_builder(env.project_id, user, session)
    await environments.transition(session, version, "submitted", actor=user)
    await session.commit()
    return version_out(version)
