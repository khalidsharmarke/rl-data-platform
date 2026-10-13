import random
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.audit import record
from app.auth import current_user
from app.db import get_session
from app.models import Allocation, Capability, Contract, GoldAnswer, Org, OrgKind, Project, Role, Task, TaskSchema, User
from app.scoping import is_internal, require_roles
from app.work import counts_by_vendor, route_tasks

router = APIRouter(prefix="/projects")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    task_type: str = Field(min_length=1, max_length=60)
    answer_schema: dict[str, Any]
    instructions: str = Field(min_length=1)
    target_volume: int = Field(gt=0)
    deadline: date | None = None
    overlap: int = Field(ge=1, le=10)
    gold_rate: Decimal = Field(ge=0, lt=1)
    quality_bar: Decimal = Field(default=Decimal("0.8"), ge=0, le=1)
    min_gold_results: int = Field(default=5, ge=1)


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    task_type: str
    current_schema_version: int
    target_volume: int
    deadline: date | None
    overlap: int
    quality_bar: Decimal
    status: str


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
    try:
        jsonschema.Draft202012Validator.check_schema(body.answer_schema)
    except jsonschema.SchemaError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid answer schema: {e.message}")
    project = Project(
        name=body.name, created_by=user.id, task_type=body.task_type, current_schema_version=1,
        target_volume=body.target_volume, deadline=body.deadline, overlap=body.overlap, gold_rate=body.gold_rate,
        quality_bar=body.quality_bar, min_gold_results=body.min_gold_results,
    )
    session.add(project)
    await session.flush()
    session.add(TaskSchema(project_id=project.id, version=1, answer_schema=body.answer_schema, instructions=body.instructions))
    record(session, actor=user, action="project.created", entity_type="project", entity_id=project.id, org_id=user.org_id,
           after={"name": project.name, "task_type": project.task_type, "schema_version": 1, "overlap": project.overlap,
                  "gold_rate": str(project.gold_rate), "target_volume": project.target_volume})
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
    schema = await session.get(TaskSchema, (project.id, project.current_schema_version))
    validator = jsonschema.Draft202012Validator(schema.answer_schema)
    for i, t in enumerate(body.tasks):
        if t.gold_answer is not None and not validator.is_valid(t.gold_answer):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Task {i}: gold answer doesn't fit the answer schema")
    # Shuffle so a task's position (and so its claim order) says nothing about whether it's gold.
    items = list(body.tasks)
    random.shuffle(items)
    gold = 0
    for t in items:
        task = Task(project_id=project.id, schema_version=project.current_schema_version, payload=t.payload)
        session.add(task)
        await session.flush()
        if t.gold_answer is not None:
            session.add(GoldAnswer(task_id=task.id, answer=t.gold_answer))
            gold += 1
    await session.flush()
    routing = await route_tasks(session, project)
    record(session, actor=user, action="tasks.added", entity_type="project", entity_id=project.id, org_id=user.org_id,
           after={"tasks": len(items), "gold": gold, "schema_version": project.current_schema_version, **routing})
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
    if Capability.HUMAN_WORK not in vendor.capabilities:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{vendor.name} doesn't supply human work")
    contract = await session.get(Contract, body.contract_id)
    if contract is None or contract.vendor_id != vendor.id or contract.kind != Capability.HUMAN_WORK:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That contract isn't a human-work contract with this vendor")
    if not contract.in_effect(clock.now().date()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That contract isn't in effect today")
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
