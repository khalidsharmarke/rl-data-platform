"""Environments and their versions (EC0–EC2, EC8, D3, D33, D34).

An environment builder works only against a brief allocated to their vendor, and only if
their vendor supplies environments (VM6). Other vendors can't see a vendor's environments
(N9); Anthropic staff see all of them.
"""

import asyncio
import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, environments, generated, registry
from app.config import settings
from app.audit import record
from app.auth import current_user
from app.db import get_session
from app.jobs import enqueue
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


class GeneratedIn(BaseModel):
    """Where a generated version came from (D48): its task family, intended difficulty and batch."""
    family: str = Field(pattern=r"^[a-z0-9_]{1,60}$")
    difficulty: str = Field(pattern="^(easy|medium|hard)$")
    batch: str = Field(min_length=1, max_length=80)


class VersionIn(BaseModel):
    test_command: str = Field(min_length=1, max_length=500)
    # Runs in the attempt step, before grading (for example, to build the task's code). Optional.
    prepare_command: str | None = Field(default=None, max_length=500)
    files: list[FileIn] = Field(min_length=1, max_length=200)
    # The vendor's own runtime image, by digest, instead of the base image (D50). Task content
    # still comes in `files`; the image holds only the runtime.
    image: str | None = Field(default=None, max_length=300)
    generated: GeneratedIn | None = None


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
    env = Environment(org_id=user.org_id, project_id=project.id, name=body.name, created_by=user.id, created_by_token=getattr(user, "token_id", None), created_at=clock.now())
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


async def pin_vendor_image(ref: str, user: User, session: AsyncSession) -> str:
    """Copy the vendor's image into the platform's namespace, so the version never depends on the vendor's copy (D50)."""
    if not settings.registry_url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "This platform has no registry; leave out image")
    org = await session.get(Org, user.org_id)
    match = registry.IMAGE_REF.match(ref)
    if not match or not match.group(1).startswith(registry.namespace(org.name) + "/"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"image must be {registry.namespace(org.name)}/<name>@sha256:<digest>, pinned by digest")
    try:
        return await asyncio.to_thread(registry.pin, ref)
    except LookupError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{ref} isn't in the registry; push it first")


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
    base_image = settings.environment_base_image
    if body.image:
        base_image = await pin_vendor_image(body.image, user, session)
    manifest = await environments.build_manifest(session, files, body.test_command, body.prepare_command, base_image)
    if body.image:
        manifest["vendor_image"] = body.image
    if body.generated:
        manifest["generated"] = body.generated.model_dump()
    now = clock.now()
    version = EnvironmentVersion(environment_id=env.id, number=await environments.next_number(session, env.id), manifest=manifest,
                                 status="draft", created_by=user.id, created_by_token=getattr(user, "token_id", None), created_at=now, status_changed_at=now)
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
    # The validation job is queued in the same transaction, so a version can't be submitted
    # without one (D2). The sandbox runner takes it, fairly across vendors (D4).
    await enqueue(session, "validate_version", f"validate_version:{version.id}",
                  {"version_id": str(version.id), "vendor_id": str(env.org_id)})
    await session.commit()
    return version_out(version)


class CheckOut(BaseModel):
    check_name: str
    passed: bool
    outcome: str
    detail: str
    duration_ms: int
    logs: str
    created_at: datetime


@router.get("/versions/{version_id}/checks")
async def version_checks(version_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> list[CheckOut]:
    """The sandbox's results for a version, with each check's logs (EC4)."""
    version, _ = await visible_version(version_id, user, session)
    rows = await session.execute(
        text("SELECT * FROM validation_runs WHERE version_id = :v "
             "ORDER BY created_at::date, array_position(ARRAY['reference', 'empty', 'repeat', 'probes'], check_name), created_at"),
        {"v": version.id}
    )
    return [CheckOut(check_name=r.check_name, passed=r.passed, outcome=r.outcome, detail=r.detail, duration_ms=r.duration_ms,
                     logs=environments.read(r.logs_hash).decode(errors="replace"), created_at=r.created_at) for r in rows]


class VersionRow(BaseModel):
    id: uuid.UUID
    # For a generated version, the batch it was generated in (D49).
    batch: str | None = None
    environment_id: uuid.UUID
    environment_name: str
    project_id: uuid.UUID
    vendor_name: str
    number: int
    status: str
    status_changed_at: datetime


@router.get("/versions")
async def list_versions_by_status(
    project_id: uuid.UUID | None = None, status_filter: str | None = Query(default=None, alias="status"),
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
) -> list[VersionRow]:
    """Versions across environments, for a brief and a status: the researcher's review queue (EC5)."""
    query = (select(EnvironmentVersion, Environment, Org.name)
             .join(Environment, Environment.id == EnvironmentVersion.environment_id)
             .join(Org, Org.id == Environment.org_id)
             .order_by(EnvironmentVersion.status_changed_at))
    if not is_internal(user):
        query = query.where(Environment.org_id == user.org_id)
    if project_id:
        query = query.where(Environment.project_id == project_id)
    else:  # onboarding samples stay out of the review queue (D46), and so do generated versions no sample drew (D49)
        query = query.where(Environment.project_id.not_in(select(Project.id).where(Project.onboarding)),
                            ~EnvironmentVersion.manifest.has_key("generated") | EnvironmentVersion.id.in_(text("SELECT version_id FROM batch_samples")))
    if status_filter:
        query = query.where(EnvironmentVersion.status == status_filter)
    return [VersionRow(id=v.id, environment_id=e.id, environment_name=e.name, project_id=e.project_id, vendor_name=name,
                       number=v.number, status=v.status, status_changed_at=v.status_changed_at,
                       batch=v.manifest.get("generated", {}).get("batch"))
            for v, e, name in (await session.execute(query)).all()]


class FileDiff(BaseModel):
    path: str
    part: str
    change: str  # added, removed, changed or unchanged
    diff: list[str] | None = None


class DiffOut(BaseModel):
    version_id: uuid.UUID
    against: uuid.UUID | None
    against_number: int | None
    files: list[FileDiff]


@router.get("/versions/{version_id}/diff")
async def version_diff(
    version_id: uuid.UUID, against: uuid.UUID | None = None,
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
) -> DiffOut:
    """What changed from another version of the same environment, file by file (EC2); by default the previous one."""
    version, env = await visible_version(version_id, user, session)
    if against is not None:
        other, other_env = await visible_version(against, user, session)
        if other_env.id != env.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Both versions must belong to the same environment")
    else:
        other = await session.scalar(select(EnvironmentVersion).where(
            EnvironmentVersion.environment_id == env.id, EnvironmentVersion.number < version.number
        ).order_by(EnvironmentVersion.number.desc()).limit(1))
    new = {f["path"]: f for f in version.manifest["files"]}
    old = {f["path"]: f for f in other.manifest["files"]} if other else {}
    files = []
    for path in sorted(set(new) | set(old), key=lambda p: (environments.PARTS.index((new.get(p) or old[p])["part"]), p)):
        a, b = old.get(path), new.get(path)
        part = (b or a)["part"]
        if a and b and a["hash"] == b["hash"]:
            files.append(FileDiff(path=path, part=part, change="unchanged"))
        elif a and b:
            files.append(FileDiff(path=path, part=part, change="changed", diff=environments.text_diff(a["hash"], b["hash"], path)))
        else:
            files.append(FileDiff(path=path, part=part, change="added" if b else "removed"))
    return DiffOut(version_id=version.id, against=other.id if other else None, against_number=other.number if other else None, files=files)


class ReviewIn(BaseModel):
    decision: str = Field(pattern="^(approve|send_back)$")
    # Every criterion in the brief's rubric, met or not.
    rubric: dict[str, bool]
    feedback: str = Field(min_length=3, max_length=4000)


class ReviewOut(BaseModel):
    decision: str
    rubric: dict[str, bool]
    feedback: str
    reviewer: str
    created_at: datetime


@router.post("/versions/{version_id}/reviews", status_code=status.HTTP_201_CREATED)
async def review_version(
    version_id: uuid.UUID, body: ReviewIn,
    user: User = Depends(require_roles(Role.RESEARCHER)), session: AsyncSession = Depends(get_session),
) -> VersionOut:
    """A researcher grades a version against its brief's rubric (EC5): approve, or send it back."""
    version, env = await visible_version(version_id, user, session)
    if version.status != "in_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a version in review can be reviewed")
    project = await session.get(Project, env.project_id)
    if project.onboarding:
        raise HTTPException(status.HTTP_409_CONFLICT, "Onboarding samples aren't reviewed; passing the checks is the test (D46)")
    if "generated" in version.manifest and not await session.scalar(
            text("SELECT 1 FROM batch_samples WHERE version_id = :v"), {"v": version.id}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Generated versions are reviewed by sample: review its batch's sample (D49)")
    criteria = project.brief["review_rubric"]
    if set(body.rubric) != set(criteria):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Answer every criterion in the brief's rubric, and only those")
    if body.decision == "approve" and not all(body.rubric.values()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Approval needs every rubric criterion met")
    await session.execute(
        text("INSERT INTO environment_reviews (version_id, reviewer_id, decision, rubric, feedback, created_at) "
             "VALUES (:v, :u, :d, CAST(:r AS jsonb), :f, :now)"),
        {"v": version.id, "u": user.id, "d": body.decision, "r": json.dumps(body.rubric), "f": body.feedback, "now": clock.now()},
    )
    await environments.transition(session, version, "approved" if body.decision == "approve" else "failed", actor=user,
                                  note=body.feedback)
    await generated.after_review(session, version, body.decision, user)
    await session.commit()
    return version_out(version)


@router.get("/versions/{version_id}/reviews")
async def version_reviews(version_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> list[ReviewOut]:
    """Reviews of a version, for its vendor (the builder's feedback, EC4) and for Anthropic."""
    version, _ = await visible_version(version_id, user, session)
    rows = await session.execute(text(
        "SELECT r.*, u.display_name FROM environment_reviews r JOIN users u ON u.id = r.reviewer_id "
        "WHERE r.version_id = :v ORDER BY r.created_at"), {"v": version.id})
    return [ReviewOut(decision=r.decision, rubric=r.rubric, feedback=r.feedback, reviewer=r.display_name, created_at=r.created_at) for r in rows]


class ProgressOut(BaseModel):
    target: int
    environments: int
    approved: int
    in_review: int
    failed: int
    in_progress: int


@router.get("/projects/{project_id}/environment-progress")
async def environment_progress(project_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> ProgressOut:
    """Environments against the brief's target, by the status of each one's latest version (M15)."""
    project = await session.get(Project, project_id)
    if project is None or project.kind != "environment":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    rows = await environment_rows(session, user, project_id=project.id)
    if not is_internal(user) and not rows and not await session.scalar(
            select(Allocation.id).where(Allocation.project_id == project.id, Allocation.vendor_id == user.org_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    latest = [r.latest_status for r in rows]
    return ProgressOut(target=project.target_volume, environments=len(rows), approved=latest.count("approved"),
                       in_review=latest.count("in_review"), failed=latest.count("failed"),
                       in_progress=sum(1 for s in latest if s in ("draft", "submitted", "validating")))
