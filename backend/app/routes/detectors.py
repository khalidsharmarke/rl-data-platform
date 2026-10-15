"""How well detection is doing, and which rule versions may act (AB15, AB16, D27, D40, D41).

Anthropic only. Rule names and thresholds never reach vendors (N17), and nothing here says
which answers were audited.
"""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, detection_quality
from app.audit import record
from app.db import get_session
from app.models import Role, User
from app.scoping import require_roles

router = APIRouter()


class RuleMeasure(BaseModel):
    rule_version: str
    detector: str
    mode: str
    bar: float | None
    changed_at: datetime
    findings: int
    subjects: int  # workers, or environment versions for the hack detector
    reviewed: int
    abusive: int
    precision: float | None
    can_promote: bool  # enough reviews, and precision at the bar (or the default bar, if none is set)


class DetectionQuality(BaseModel):
    rules: list[RuleMeasure]
    audited: int
    audited_abusive: int
    miss_rate: float | None
    min_reviewed: int


class ModeIn(BaseModel):
    mode: str = Field(pattern="^(shadow|live)$")
    bar: float | None = Field(default=None, gt=0, le=1)
    reason: str = Field(min_length=3, max_length=1000)


def measure(r) -> RuleMeasure:
    precision = r.abusive / r.reviewed if r.reviewed else None
    bar = float(r.bar) if r.bar is not None else None
    return RuleMeasure(
        rule_version=r.rule_version, detector=r.detector, mode=r.mode, bar=bar, changed_at=r.changed_at, findings=r.findings,
        subjects=r.subjects, reviewed=r.reviewed, abusive=r.abusive, precision=precision,
        can_promote=(r.mode == "shadow" and r.reviewed >= detection_quality.MIN_REVIEWED
                     and precision >= (bar if bar is not None else detection_quality.DEFAULT_BAR)),
    )


@router.get("/detection-quality")
async def detection_quality_view(user: User = Depends(require_roles(Role.DATA_OPS, Role.RESEARCHER)),
                                 session: AsyncSession = Depends(get_session)) -> DetectionQuality:
    m = await detection_quality.misses(session)
    return DetectionQuality(
        rules=[measure(r) for r in await detection_quality.rule_measures(session)],
        audited=m.audited, audited_abusive=m.abusive, miss_rate=m.abusive / m.audited if m.audited else None,
        min_reviewed=detection_quality.MIN_REVIEWED,
    )


@router.put("/detectors/{rule_version}/mode")
async def set_mode(rule_version: str, body: ModeIn, user: User = Depends(require_roles(Role.DATA_OPS)),
                   session: AsyncSession = Depends(get_session)) -> RuleMeasure:
    """Promote a version to live once it has earned it, or send one back to shadow at any time (D41)."""
    rows = await detection_quality.rule_measures(session, rule_version)
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such rule version")
    current = measure(rows[0])
    bar = body.bar if body.bar is not None else current.bar
    if body.mode == "live":
        if current.mode == "live":
            raise HTTPException(status.HTTP_409_CONFLICT, "Already live")
        if bar is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Set the precision bar this version must meet")
        if current.reviewed < detection_quality.MIN_REVIEWED:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Only {current.reviewed} of its flagged answers have been reviewed; it needs {detection_quality.MIN_REVIEWED}")
        if current.precision < bar:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"Its precision is {current.precision:.0%}, below the bar of {bar:.0%}")
    await session.execute(
        text("UPDATE rule_modes SET mode = :m, bar = :b, changed_by = :u, changed_at = :now WHERE rule_version = :v"),
        {"m": body.mode, "b": Decimal(str(bar)) if bar is not None else None, "u": user.id, "now": clock.now(), "v": rule_version},
    )
    # Anthropic's log only: vendors never learn rule names (N17).
    record(session, actor=user, action=f"rule.{body.mode}", entity_type="rule_version", entity_id=rule_version, org_id=None,
           before={"mode": current.mode}, after={"mode": body.mode, "bar": bar, "precision": current.precision,
                                                  "reviewed": current.reviewed, "reason": body.reason})
    await session.commit()
    return measure((await detection_quality.rule_measures(session, rule_version))[0])
