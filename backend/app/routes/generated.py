"""Generated batches, their samples and task families (EC7, D49)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, generated
from app.audit import record
from app.auth import current_user
from app.db import get_session
from app.models import Role, User
from app.routes.environments import require_brief_for_builder
from app.routes.projects import get_visible_project
from app.scoping import require_roles

router = APIRouter()


@router.post("/projects/{project_id}/generated-batches/{batch}/ready", status_code=status.HTTP_201_CREATED)
async def batch_ready(project_id: uuid.UUID, batch: str, user: User = Depends(require_roles(Role.ENVIRONMENT_BUILDER)),
                      session: AsyncSession = Depends(get_session)) -> dict:
    """The generator says a batch is complete; the platform draws the sample researchers review."""
    await require_brief_for_builder(project_id, user, session)
    if await session.scalar(text("SELECT 1 FROM generated_batches WHERE project_id = :p AND batch = :b"), {"p": project_id, "b": batch}):
        raise HTTPException(status.HTTP_409_CONFLICT, "That batch is already in review")
    out = await generated.ready(session, project_id, batch, user.org_id, user)
    await session.commit()
    return out


@router.get("/projects/{project_id}/generated")
async def generated_summary(project_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    """Batches and their samples, stopped families, and intended against measured difficulty per family (EC3)."""
    project = await get_visible_project(project_id, user, session)
    return await generated.summary(session, project.id)


@router.post("/family-stops/{stop_id}/resume")
async def resume_family(stop_id: int, user: User = Depends(require_roles(Role.RESEARCHER)), session: AsyncSession = Depends(get_session)) -> dict:
    """The template is fixed: the family may generate again."""
    row = (await session.execute(text("UPDATE family_stops SET resumed_at = :now, resumed_by = :u WHERE id = :i AND resumed_at IS NULL "
                                      "RETURNING org_id, family"), {"now": clock.now(), "u": user.id, "i": stop_id})).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such stopped family")
    record(session, actor=user, action="task_family.resumed", entity_type="task_family", entity_id=row.family, org_id=row.org_id)
    await session.commit()
    return {"family": row.family, "resumed": True}


class StopIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/projects/{project_id}/task-families/{family}/stop", status_code=status.HTTP_201_CREATED)
async def stop_family(project_id: uuid.UUID, family: str, body: StopIn, user: User = Depends(require_roles(Role.ENVIRONMENT_BUILDER)),
                      session: AsyncSession = Depends(get_session)) -> dict:
    """The generator stops one of its own families, so the stop shows on the brief and a researcher resumes it."""
    await require_brief_for_builder(project_id, user, session)
    await generated.stop_family(session, user.org_id, family, body.reason, user)
    await session.commit()
    return {"family": family, "stopped": True}
