import uuid
from datetime import datetime
from typing import Any

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.db import get_session
from app.models import Allocation, Assignment, Contract, Org, Project, Role, Submission, Task, TaskSchema, User
from app.scoping import can_see_org, require_roles, shows_identity
from app.work import claim_next

router = APIRouter()


class NextIn(BaseModel):
    project_id: uuid.UUID | None = None


class TaskView(BaseModel):
    """What a worker sees. Deliberately identical for gold and regular tasks (D8)."""

    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    schema_version: int
    instructions: str
    answer_schema: dict[str, Any]
    payload: dict[str, Any]


class AssignmentOut(BaseModel):
    id: uuid.UUID
    expires_at: datetime
    task: TaskView


class SubmitIn(BaseModel):
    answer: dict[str, Any]
    # Behavior the browser observed (e.g. pasted text). Used by abuse rules later; never trusted for timing.
    client_signals: dict[str, Any] | None = None


class SubmissionOut(BaseModel):
    id: uuid.UUID
    submitted_at: datetime
    time_on_task_ms: int


class LineageOut(BaseModel):
    submission_id: uuid.UUID
    task_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    schema_version: int
    vendor_id: uuid.UUID
    vendor_name: str
    worker_handle: str
    worker_name: str | None
    allocation_id: uuid.UUID
    contract_id: uuid.UUID
    contract_price: str
    contract_price_unit: str
    assigned_at: datetime
    submitted_at: datetime
    time_on_task_ms: int


@router.post("/assignments/next", response_model=AssignmentOut, responses={204: {"description": "No work available"}})
async def next_task(
    body: NextIn, user: User = Depends(require_roles(Role.WORKER)), session: AsyncSession = Depends(get_session)
):
    if user.suspended_at is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account is suspended; contact your vendor admin")
    assignment = await claim_next(session, worker_id=user.id, vendor_id=user.org_id, project_id=body.project_id)
    if assignment is None:
        await session.rollback()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    task = await session.get(Task, assignment.task_id)
    project = await session.get(Project, task.project_id)
    schema = await session.get(TaskSchema, (task.project_id, task.schema_version))
    await session.commit()
    return AssignmentOut(
        id=assignment.id,
        expires_at=assignment.expires_at,
        task=TaskView(
            id=task.id, project_id=project.id, project_name=project.name, schema_version=task.schema_version,
            instructions=schema.instructions, answer_schema=schema.answer_schema, payload=task.payload,
        ),
    )


@router.post("/assignments/{assignment_id}/submit", status_code=status.HTTP_201_CREATED)
async def submit(
    assignment_id: uuid.UUID,
    body: SubmitIn,
    user: User = Depends(require_roles(Role.WORKER)),
    session: AsyncSession = Depends(get_session),
) -> SubmissionOut:
    if user.suspended_at is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account is suspended; contact your vendor admin")
    assignment = await session.scalar(select(Assignment).where(Assignment.id == assignment_id).with_for_update())
    if assignment is None or assignment.worker_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    now = clock.now()
    if assignment.status != "open" or assignment.expires_at <= now:
        raise HTTPException(status.HTTP_409_CONFLICT, "This assignment is no longer open")
    task = await session.get(Task, assignment.task_id)
    schema = await session.get(TaskSchema, (task.project_id, task.schema_version))
    errors = sorted(jsonschema.Draft202012Validator(schema.answer_schema).iter_errors(body.answer), key=str)
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Answer doesn't fit the task: {errors[0].message}")
    allocation = await session.get(Allocation, assignment.allocation_id)
    submission = Submission(
        assignment_id=assignment.id, task_id=task.id, project_id=task.project_id, schema_version=task.schema_version,
        vendor_id=assignment.vendor_id, worker_id=user.id, allocation_id=allocation.id, contract_id=allocation.contract_id,
        answer=body.answer, client_signals=body.client_signals, assigned_at=assignment.assigned_at, submitted_at=now,
        # Measured by the platform, from assignment to submission (DC9, PRD PD3).
        time_on_task_ms=int((now - assignment.assigned_at).total_seconds() * 1000),
    )
    assignment.status = "submitted"
    session.add(submission)
    await session.commit()
    return SubmissionOut(id=submission.id, submitted_at=submission.submitted_at, time_on_task_ms=submission.time_on_task_ms)


@router.get("/submissions/{submission_id}/lineage")
async def lineage(
    submission_id: uuid.UUID,
    user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS, Role.VENDOR_ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> LineageOut:
    submission = await session.get(Submission, submission_id)
    if submission is None or not can_see_org(user, submission.vendor_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    worker = await session.get(User, submission.worker_id)
    vendor = await session.get(Org, submission.vendor_id)
    project = await session.get(Project, submission.project_id)
    contract = await session.get(Contract, submission.contract_id)
    return LineageOut(
        submission_id=submission.id, task_id=submission.task_id, project_id=project.id, project_name=project.name,
        schema_version=submission.schema_version, vendor_id=vendor.id, vendor_name=vendor.name,
        worker_handle=worker.handle, worker_name=worker.display_name if shows_identity(user, worker) else None,
        allocation_id=submission.allocation_id, contract_id=contract.id, contract_price=str(contract.price),
        contract_price_unit=contract.price_unit, assigned_at=submission.assigned_at, submitted_at=submission.submitted_at,
        time_on_task_ms=submission.time_on_task_ms,
    )
