import random
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, environments, onboarding, task_types
from app.audit import record
from app.auth import current_user
from app.db import get_session
from app.models import Allocation, Capability, Contract, GoldAnswer, Org, OrgKind, Project, Role, Task, TaskSchema, User
from app.scoping import is_internal, require_roles
from app.work import counts_by_vendor, route_tasks

router = APIRouter(prefix="/projects")


class BriefIn(BaseModel):
    """What a researcher asks environment vendors for (EC0)."""

    description: str = Field(min_length=1)
    languages: list[str] = []
    constraints: list[str] = []
    # Share of the target count at each difficulty, e.g. {"easy": 0.3, "medium": 0.5, "hard": 0.2}.
    difficulty_mix: dict[str, float] = {}
    # What reviewers grade each environment against (EC5).
    review_rubric: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def mix_adds_up(self) -> "BriefIn":
        if self.difficulty_mix and abs(sum(self.difficulty_mix.values()) - 1) > 0.01:
            raise ValueError("The difficulty mix must add up to 1")
        return self


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # D33: a human-data project or an environment brief.
    kind: str = Field(default="human_data", pattern="^(human_data|environment)$")
    target_volume: int = Field(gt=0)
    deadline: date | None = None
    # Human data only.
    task_type: str | None = Field(default=None, min_length=1, max_length=60)
    answer_schema: dict[str, Any] | None = None
    instructions: str | None = Field(default=None, min_length=1)
    overlap: int | None = Field(default=None, ge=1, le=10)
    gold_rate: Decimal | None = Field(default=None, ge=0, lt=1)
    quality_bar: Decimal = Field(default=Decimal("0.8"), ge=0, le=1)
    min_gold_results: int = Field(default=5, ge=1)
    release_after_minutes: int = Field(default=60, ge=0)
    release_cap_minutes: int = Field(default=24 * 60, ge=0)
    # Environment briefs only.
    brief: BriefIn | None = None

    @model_validator(mode="after")
    def fits_its_kind(self) -> "ProjectIn":
        if self.release_cap_minutes < self.release_after_minutes:
            raise ValueError("The release cap can't be shorter than the minimum gap")
        human = (self.task_type, self.instructions, self.overlap, self.gold_rate)
        if self.kind == "human_data" and (any(v is None for v in human) or self.brief is not None):
            raise ValueError("A human-data project needs task_type, instructions, overlap and gold_rate, and no brief")
        if self.kind == "environment" and (self.brief is None or self.answer_schema is not None or any(v is not None for v in human)):
            raise ValueError("An environment brief needs a brief and none of the human-data fields")
        return self


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    brief: dict[str, Any] | None
    task_type: str
    current_schema_version: int
    target_volume: int
    deadline: date | None
    overlap: int
    quality_bar: Decimal
    status: str
    onboarding: bool


class TaskIn(BaseModel):
    payload: dict[str, Any]
    # Present only for gold tasks. Stored apart from the task and never shown to vendors or workers.
    gold_answer: dict[str, Any] | None = None


class TasksIn(BaseModel):
    tasks: list[TaskIn] = Field(min_length=1, max_length=5000)


class AllocationIn(BaseModel):
    vendor_id: uuid.UUID
    contract_id: uuid.UUID
    share: Decimal = Field(gt=0, le=1)


class AllocationPatch(BaseModel):
    share: Decimal | None = Field(default=None, gt=0, le=1)
    status: str | None = Field(default=None, pattern="^(active|paused|ended)$")


class AllocationOut(BaseModel):
    id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_name: str
    contract_id: uuid.UUID
    share: Decimal
    status: str
    vendor_review: bool
    routed_tasks: int
    submissions: int


def vendor_visible(routing: dict[str, int]) -> dict[str, int]:
    """Routing stats safe for an audit event a vendor can read: no gold counts (N17)."""
    return {k: v for k, v in routing.items() if "gold" not in k}


def project_out(p: Project) -> ProjectOut:
    return ProjectOut.model_validate(p, from_attributes=True)


async def get_visible_project(project_id: uuid.UUID, user: User, session: AsyncSession) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not is_internal(user):
        allocated = await session.scalar(
            select(Allocation.id).where(Allocation.project_id == project.id, Allocation.vendor_id == user.org_id)
        )
        if allocated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return project


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectIn, user: User = Depends(require_roles(Role.RESEARCHER)), session: AsyncSession = Depends(get_session)
) -> ProjectOut:
    if body.kind == "environment":
        return await create_brief(body, user, session)
    task_type = task_types.TYPES.get(body.task_type)
    if task_type is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Task type must be one of {', '.join(task_types.TYPES)}")
    # A researcher may narrow the type's schema (say, require a reason), but the fields its measures read must stay (D53).
    answer_schema = body.answer_schema or task_type.schema
    try:
        jsonschema.Draft202012Validator.check_schema(answer_schema)
    except jsonschema.SchemaError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid answer schema: {e.message}")
    if missing := set(task_type.requires) - set(answer_schema.get("required", [])):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"A {task_type.label.lower()} schema must require {', '.join(sorted(missing))}")
    project = Project(
        name=body.name, created_by=user.id, task_type=body.task_type, current_schema_version=1,
        target_volume=body.target_volume, deadline=body.deadline, overlap=body.overlap, gold_rate=body.gold_rate,
        quality_bar=body.quality_bar, min_gold_results=body.min_gold_results,
        release_after_minutes=body.release_after_minutes, release_cap_minutes=body.release_cap_minutes,
    )
    session.add(project)
    await session.flush()
    session.add(TaskSchema(project_id=project.id, version=1, answer_schema=answer_schema, instructions=body.instructions))
    record(session, actor=user, action="project.created", entity_type="project", entity_id=project.id, org_id=user.org_id,
           after={"name": project.name, "task_type": project.task_type, "schema_version": 1, "overlap": project.overlap,
                  "gold_rate": str(project.gold_rate), "target_volume": project.target_volume})
    await session.commit()
    return project_out(project)


async def create_brief(body: ProjectIn, user: User, session: AsyncSession) -> ProjectOut:
    """An environment brief (EC0, D33). The human-data columns hold neutral values."""
    project = Project(
        name=body.name, created_by=user.id, kind="environment", brief=body.brief.model_dump(), task_type="environment",
        current_schema_version=0, target_volume=body.target_volume, deadline=body.deadline, overlap=1, gold_rate=Decimal("0"),
    )
    session.add(project)
    await session.flush()
    record(session, actor=user, action="project.created", entity_type="project", entity_id=project.id, org_id=user.org_id,
           after={"name": project.name, "kind": "environment", "target_volume": project.target_volume})
    await session.commit()
    return project_out(project)


@router.get("")
async def list_projects(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> list[ProjectOut]:
    query = select(Project).order_by(Project.created_at.desc())
    if not is_internal(user):
        query = query.where(Project.id.in_(select(Allocation.project_id).where(Allocation.vendor_id == user.org_id)))
    return [project_out(p) for p in await session.scalars(query)]


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> ProjectOut:
    return project_out(await get_visible_project(project_id, user, session))


@router.post("/{project_id}/tasks", status_code=status.HTTP_201_CREATED)
async def add_tasks(
    project_id: uuid.UUID,
    body: TasksIn,
    user: User = Depends(require_roles(Role.RESEARCHER)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    project = await get_visible_project(project_id, user, session)
    if project.kind != "human_data":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Tasks belong to human-data projects; vendors build environments against a brief")
    return await insert_tasks(session, project, body.tasks, user)


class EpisodeTaskIn(BaseModel):
    episode_id: str
    gold_answer: dict[str, Any] | None = None


class EpisodeTasksIn(BaseModel):
    episodes: list[EpisodeTaskIn] = Field(min_length=1, max_length=500)


@router.post("/{project_id}/tasks/from-episodes", status_code=status.HTTP_201_CREATED)
async def add_episode_tasks(
    project_id: uuid.UUID, body: EpisodeTasksIn, user: User = Depends(require_roles(Role.RESEARCHER)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Trajectory review tasks from training's episodes (D54, TO5): each transcript becomes numbered steps,
    with what the grader said; the episode's id stays in the task, so a review traces back to the run."""
    project = await get_visible_project(project_id, user, session)
    if project.task_type != "trajectory_review":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Episodes become tasks only in a trajectory review project")
    tasks = []
    for e in body.episodes:
        row = (await session.execute(text(
            "SELECT e.episode_id, e.passed, e.transcript_hash, env.name AS environment_name FROM episodes e "
            "JOIN environment_versions v ON v.id = e.version_id JOIN environments env ON env.id = v.environment_id "
            "WHERE e.episode_id = :id"), {"id": e.episode_id})).first()
        if row is None or row.transcript_hash is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{e.episode_id}: no such episode, or it has no transcript")
        steps = [line for line in environments.read(row.transcript_hash).decode(errors="replace").splitlines() if line.strip()]
        if e.gold_answer and not 0 <= e.gold_answer.get("first_wrong_step", -1) <= len(steps):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{e.episode_id}: the gold answer's step isn't in the transcript")
        tasks.append(TaskIn(gold_answer=e.gold_answer, payload={
            "item": row.episode_id, "episode_id": row.episode_id, "environment": row.environment_name, "steps": steps,
            "graded": "passed" if row.passed else "failed",
            "prompt": f"An agent worked on \"{row.environment_name}\". Find the first step that goes wrong, if any, and judge the outcome."}))
    return await insert_tasks(session, project, tasks, user, source="episodes")


async def insert_tasks(session: AsyncSession, project: Project, tasks: list[TaskIn], user: User, **audit) -> dict[str, int]:
    """Add tasks to a project, gold stored apart, shuffled and routed."""
    if project.task_type in task_types.WITHOUT_GOLD and any(t.gold_answer is not None for t in tasks):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Writing tasks have no gold answers; researchers' spot checks measure them")
    schema = await session.get(TaskSchema, (project.id, project.current_schema_version))
    validator = jsonschema.Draft202012Validator(schema.answer_schema)
    for i, t in enumerate(tasks):
        if t.gold_answer is not None and not validator.is_valid(t.gold_answer):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Task {i}: gold answer doesn't fit the answer schema")
    # Shuffle so a task's position (and so its claim order) says nothing about whether it's gold.
    items = list(tasks)
    random.shuffle(items)
    gold = 0
    batch_id = uuid.uuid4()  # one per upload: a batch with low agreement points at a problem in how it was made (D43)
    for t in items:
        task = Task(project_id=project.id, schema_version=project.current_schema_version, payload=t.payload, batch_id=batch_id)
        session.add(task)
        await session.flush()
        if t.gold_answer is not None:
            session.add(GoldAnswer(task_id=task.id, answer=t.gold_answer))
            gold += 1
    await session.flush()
    routing = await route_tasks(session, project)
    record(session, actor=user, action="tasks.added", entity_type="project", entity_id=project.id, org_id=user.org_id,
           after={"tasks": len(items), "gold": gold, "schema_version": project.current_schema_version, **routing, **audit})
    await session.commit()
    return {"tasks": len(items), "gold": gold}


async def allocation_rows(session: AsyncSession, project: Project) -> list[AllocationOut]:
    counts = await counts_by_vendor(session, project.id)
    rows = await session.execute(
        select(Allocation, Org.name).join(Org, Org.id == Allocation.vendor_id).where(Allocation.project_id == project.id).order_by(Org.name)
    )
    return [
        AllocationOut(
            id=a.id, vendor_id=a.vendor_id, vendor_name=name, contract_id=a.contract_id, share=a.share, status=a.status,
            vendor_review=a.vendor_review,
            **counts.get(a.vendor_id, {"routed_tasks": 0, "submissions": 0}),
        )
        for a, name in rows
    ]


@router.get("/{project_id}/allocations")
async def list_allocations(
    project_id: uuid.UUID, user: User = Depends(require_roles(Role.DATA_OPS, Role.RESEARCHER)), session: AsyncSession = Depends(get_session)
) -> list[AllocationOut]:
    return await allocation_rows(session, await get_visible_project(project_id, user, session))


@router.post("/{project_id}/allocations", status_code=status.HTTP_201_CREATED)
async def allocate(
    project_id: uuid.UUID,
    body: AllocationIn,
    user: User = Depends(require_roles(Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> list[AllocationOut]:
    project = await get_visible_project(project_id, user, session)
    vendor = await session.get(Org, body.vendor_id)
    if vendor is None or vendor.kind != OrgKind.VENDOR:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such vendor")
    # The vendor must supply this kind of work, under a contract for it (VP1, VM6, VM7, D33).
    needed = Capability.ENVIRONMENTS if project.kind == "environment" else Capability.HUMAN_WORK
    work = "environments" if needed == Capability.ENVIRONMENTS else "human work"
    if needed not in vendor.capabilities:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{vendor.name} doesn't supply {work}")
    contract = await session.get(Contract, body.contract_id)
    if contract is None or contract.vendor_id != vendor.id or contract.kind != needed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"That contract isn't a {work} contract with this vendor")
    if not contract.in_effect(clock.now().date()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That contract isn't in effect today")
    # Real work waits for a complete onboarding checklist (D46).
    if not project.onboarding and vendor.onboarded_at is None and not (await onboarding.checklist(session, vendor))["complete"]:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{vendor.name} hasn't finished onboarding yet")
    if await session.scalar(select(Allocation.id).where(Allocation.project_id == project.id, Allocation.vendor_id == vendor.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "This vendor is already allocated; change its allocation instead")
    allocation = Allocation(project_id=project.id, vendor_id=vendor.id, contract_id=contract.id, share=body.share)
    session.add(allocation)
    await session.flush()
    routing = await route_tasks(session, project)
    record(session, actor=user, action="allocation.created", entity_type="allocation", entity_id=allocation.id, org_id=vendor.id,
           after={"project_id": str(project.id), "contract_id": str(contract.id), "share": str(allocation.share), **vendor_visible(routing)})
    await session.commit()
    return await allocation_rows(session, project)


@router.patch("/{project_id}/allocations/{allocation_id}")
async def change_allocation(
    project_id: uuid.UUID,
    allocation_id: uuid.UUID,
    body: AllocationPatch,
    user: User = Depends(require_roles(Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> list[AllocationOut]:
    project = await get_visible_project(project_id, user, session)
    allocation = await session.get(Allocation, allocation_id)
    if allocation is None or allocation.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    before = {"share": str(allocation.share), "status": allocation.status}
    if body.share is not None:
        allocation.share = body.share
    if body.status is not None:
        allocation.status = body.status
    await session.flush()
    routing = await route_tasks(session, project)
    record(session, actor=user, action="allocation.changed", entity_type="allocation", entity_id=allocation.id, org_id=allocation.vendor_id,
           before=before, after={"share": str(allocation.share), "status": allocation.status, **vendor_visible(routing)})
    await session.commit()
    return await allocation_rows(session, project)
