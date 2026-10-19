"""Vendor review (DC8, D44, A29): a vendor that wants to check its workers' output before it
reaches Anthropic switches review on for an allocation. Its workers' answers then wait for one of
its reviewers, who accepts each or corrects it.

A correction is a second version of the answer; the worker's own is never overwritten. Detection,
gold accuracy and the worker's grade judge the worker's own answer, so a reviewer can't hide a
worker's abuse or low accuracy from Anthropic. The vendor's scorecard, acceptance and datasets use
the answer the vendor submitted. Reviewers see every task the same way: nothing marks gold.
"""

import json
import uuid
from datetime import datetime
from typing import Any

import jsonschema
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.audit import record
from app.db import get_session
from app.models import Allocation, Role, TaskSchema, User
from app.scoping import require_roles

router = APIRouter()


class ReviewSwitch(BaseModel):
    on: bool


class QueueItem(BaseModel):
    submission_id: uuid.UUID
    project_name: str
    worker_handle: str
    worker_name: str
    submitted_at: datetime
    task_type: str
    payload: dict[str, Any]
    answer: dict[str, Any]


class Queue(BaseModel):
    waiting: int
    items: list[QueueItem]


class VendorReviewIn(BaseModel):
    decision: str = Field(pattern="^(accepted|corrected)$")
    answer: dict[str, Any] | None = None  # the corrected answer
    note: str | None = Field(default=None, max_length=1000)


WAITING = """
FROM submissions s
JOIN allocations al ON al.id = s.allocation_id AND al.vendor_review
JOIN tasks t ON t.id = s.task_id
JOIN projects p ON p.id = s.project_id
JOIN users u ON u.id = s.worker_id
WHERE s.vendor_id = :v AND NOT EXISTS (SELECT 1 FROM vendor_reviews vr WHERE vr.submission_id = s.id)
  AND NOT EXISTS (SELECT 1 FROM removals rm WHERE rm.submission_id = s.id)
"""


@router.put("/allocations/{allocation_id}/vendor-review")
async def switch_review(allocation_id: uuid.UUID, body: ReviewSwitch, user: User = Depends(require_roles(Role.VENDOR_ADMIN)),
                        session: AsyncSession = Depends(get_session)) -> dict:
    allocation = await session.get(Allocation, allocation_id)
    if allocation is None or allocation.vendor_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    allocation.vendor_review = body.on
    record(session, actor=user, action="allocation.vendor_review", entity_type="allocation", entity_id=allocation.id,
           org_id=user.org_id, after={"vendor_review": body.on})
    await session.commit()
    return {"vendor_review": body.on}


class MyAllocation(BaseModel):
    id: uuid.UUID
    share: float
    status: str
    vendor_review: bool


@router.get("/projects/{project_id}/my-allocation")
async def my_allocation(project_id: uuid.UUID, user: User = Depends(require_roles(Role.VENDOR_ADMIN, Role.VENDOR_REVIEWER)),
                        session: AsyncSession = Depends(get_session)) -> MyAllocation:
    """Your vendor's allocation on a project: its share, status and whether you review answers."""
    allocation = (await session.execute(
        text("SELECT id, share, status, vendor_review FROM allocations WHERE project_id = :p AND vendor_id = :v"),
        {"p": project_id, "v": user.org_id})).first()
    if allocation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return MyAllocation(id=allocation.id, share=float(allocation.share), status=allocation.status, vendor_review=allocation.vendor_review)


@router.get("/vendor-review/queue")
async def queue(n: int = Query(10, ge=1, le=50), user: User = Depends(require_roles(Role.VENDOR_REVIEWER, Role.VENDOR_ADMIN)),
                session: AsyncSession = Depends(get_session)) -> Queue:
    """Your vendor's answers waiting for review, oldest first."""
    waiting = await session.scalar(text("SELECT count(*) " + WAITING), {"v": user.org_id})
    rows = await session.execute(
        text("SELECT s.id, p.name AS project_name, p.task_type, u.handle, u.display_name, s.submitted_at, t.payload, s.answer "
             + WAITING + " ORDER BY s.submitted_at, s.id LIMIT :n"), {"v": user.org_id, "n": n})
    return Queue(waiting=waiting, items=[
        QueueItem(submission_id=r.id, project_name=r.project_name, worker_handle=r.handle, worker_name=r.display_name,
                  submitted_at=r.submitted_at, task_type=r.task_type, payload=r.payload, answer=r.answer) for r in rows])


@router.post("/vendor-review/{submission_id}", status_code=status.HTTP_201_CREATED)
async def review(submission_id: uuid.UUID, body: VendorReviewIn, user: User = Depends(require_roles(Role.VENDOR_REVIEWER, Role.VENDOR_ADMIN)),
                 session: AsyncSession = Depends(get_session)) -> dict:
    row = (await session.execute(text("SELECT s.id, s.answer, s.project_id, t.schema_version " + WAITING.replace(
        "WHERE s.vendor_id = :v", "WHERE s.vendor_id = :v AND s.id = :s")), {"v": user.org_id, "s": submission_id})).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No answer of yours waiting for review with that id")
    corrected = None
    if body.decision == "corrected":
        schema = await session.get(TaskSchema, (row.project_id, row.schema_version))
        if body.answer is None or not jsonschema.Draft202012Validator(schema.answer_schema).is_valid(body.answer):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A correction needs an answer that fits the task")
        if body.answer == row.answer:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That's the worker's answer; accept it instead")
        corrected = body.answer
    await session.execute(
        text("INSERT INTO vendor_reviews (submission_id, reviewer_id, decision, corrected_answer, note, created_at) "
             "VALUES (:s, :u, :d, CAST(:a AS jsonb), :n, :now)"),
        {"s": submission_id, "u": user.id, "d": body.decision, "a": None if corrected is None else json.dumps(corrected),
         "n": body.note, "now": clock.now()},
    )
    record(session, actor=user, action=f"vendor_review.{body.decision}", entity_type="submission", entity_id=submission_id,
           org_id=user.org_id, after={"note": body.note})
    await session.commit()
    return {"decision": body.decision}
