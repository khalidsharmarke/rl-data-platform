"""The catalog: where training gets approved environments (EC6, D10, EC8, N21, N22).

Only training infrastructure can call these routes; it signs in with its own token, like the
detection service. It sees approved versions only, by exact id, and receives the task files and
the grader, never the reference solution. Every pull is recorded against the version, so
lineage can say which runs used it (N22).
"""

import base64
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, environments
from app.audit import record
from app.config import settings
from app.db import get_session
from app.models import Environment, EnvironmentVersion, Role, User
from app.scoping import require_roles

router = APIRouter()
bearer = HTTPBearer(auto_error=False)
FOR_TRAINING = ("task", "grader")


def require_training(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    expected = settings.training_token
    if not expected or credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not training infrastructure")


class CatalogFile(BaseModel):
    path: str
    part: str
    hash: str
    size: int


class CatalogEntry(BaseModel):
    version_id: uuid.UUID
    environment_id: uuid.UUID
    environment_name: str
    project_id: uuid.UUID
    number: int
    base_image: str
    test_command: str
    prepare_command: str | None
    files: list[CatalogFile]


class PullIn(BaseModel):
    run_label: str | None = Field(default=None, max_length=200)


class PulledFile(CatalogFile):
    content_b64: str


class PullOut(CatalogEntry):
    files: list[PulledFile]


def entry(version: EnvironmentVersion, env: Environment) -> CatalogEntry:
    m = version.manifest
    return CatalogEntry(version_id=version.id, environment_id=env.id, environment_name=env.name, project_id=env.project_id,
                        number=version.number, base_image=m["base_image"], test_command=m["test_command"],
                        prepare_command=m.get("prepare_command"),
                        files=[CatalogFile(**f) for f in m["files"] if f["part"] in FOR_TRAINING])


@router.get("/catalog", dependencies=[Depends(require_training)])
async def catalog(project_id: uuid.UUID | None = None, session: AsyncSession = Depends(get_session)) -> list[CatalogEntry]:
    query = (select(EnvironmentVersion, Environment).join(Environment, Environment.id == EnvironmentVersion.environment_id)
             .where(EnvironmentVersion.status == "approved").order_by(Environment.name, EnvironmentVersion.number))
    if project_id:
        query = query.where(Environment.project_id == project_id)
    return [entry(v, e) for v, e in (await session.execute(query)).all()]


@router.post("/catalog/{version_id}/pull", dependencies=[Depends(require_training)])
async def pull(version_id: uuid.UUID, body: PullIn, session: AsyncSession = Depends(get_session)) -> PullOut:
    """One exact, approved version's task files and grader. The pull is recorded (N22)."""
    version = await session.get(EnvironmentVersion, version_id)
    if version is None or version.status != "approved":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No approved version with that id")
    env = await session.get(Environment, version.environment_id)
    await session.execute(
        text("INSERT INTO catalog_pulls (version_id, puller, run_label, pulled_at) VALUES (:v, 'training', :r, :now)"),
        {"v": version.id, "r": body.run_label, "now": clock.now()},
    )
    record(session, actor=None, action="catalog.pulled", entity_type="environment_version", entity_id=version.id,
           org_id=env.org_id, after={"run_label": body.run_label})
    await session.commit()
    e = entry(version, env)
    files = [PulledFile(**f.model_dump(), content_b64=base64.b64encode(environments.read(f.hash)).decode()) for f in e.files]
    return PullOut(**{**e.model_dump(), "files": files})


class PullRow(BaseModel):
    puller: str
    run_label: str | None
    pulled_at: str


@router.get("/versions/{version_id}/pulls")
async def version_pulls(
    version_id: uuid.UUID, user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> list[PullRow]:
    """Who received this version from the catalog (lineage, N22). Anthropic only."""
    rows = await session.execute(text("SELECT puller, run_label, pulled_at FROM catalog_pulls WHERE version_id = :v ORDER BY id"),
                                 {"v": version_id})
    return [PullRow(puller=r.puller, run_label=r.run_label, pulled_at=r.pulled_at.isoformat()) for r in rows]
