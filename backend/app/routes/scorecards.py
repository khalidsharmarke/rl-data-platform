import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.auth import current_user
from app.config import settings
from app.db import get_session
from app.models import Org, OrgKind, Role, User
from app.quality import METRIC_VERSION, SUBMISSION_FACTS
from app.routes.projects import get_visible_project
from app.scorecards import Point, drop_alerts, gold_cutoff
from app.scoping import is_internal, require_roles, shows_identity

router = APIRouter()

MIN_GOLD_FOR_WORKER_ACCURACY = 20  # PRD PD2


class VendorComparison(BaseModel):
    vendor_id: uuid.UUID
    vendor_name: str
    submissions: int
    accepted: int
    acceptance_rate: float | None
    gold_n: int
    gold_accuracy: float | None
    agreement: float | None
    rejected: int
    cost: float
    cost_per_accepted: float | None
    avg_turnaround_minutes: float | None
    avg_time_on_task_s: float | None
    # Vendor review (D44): gold accuracy above is on what the vendor submitted; this is on its workers' own answers.
    workers_gold_accuracy: float | None
    vendor_reviewed: int
    vendor_corrected: int


class ComparisonOut(BaseModel):
    metric_version: str
    computed_at: datetime | None
    vendors: list[VendorComparison]


class PointOut(BaseModel):
    period_start: datetime
    submissions: int
    accepted: int
    gold_n: int | None
    gold_accuracy: float | None
    agreement: float | None
    cost_per_accepted: float | None


class ScorecardOut(BaseModel):
    metric_version: str
    vendor_name: str
    gold_shown_until: datetime | None
    points: list[PointOut]
    alerts: list[dict]


class WorkerRow(BaseModel):
    worker_id: uuid.UUID
    handle: str
    display_name: str | None
    submissions: int
    agreement: float | None
    rejected: int
    gold_accuracy: float | None
    gold_n: int | None


def rate(a: float, b: float) -> float | None:
    return a / b if b else None


@router.get("/projects/{project_id}/vendors")
async def compare_vendors(
    project_id: uuid.UUID,
    user: User = Depends(require_roles(Role.DATA_OPS, Role.RESEARCHER)),
    session: AsyncSession = Depends(get_session),
) -> ComparisonOut:
    """VP2: every vendor on one project, side by side, by the same measures."""
    project = await get_visible_project(project_id, user, session)
    rows = await session.execute(
        text(
            """
            SELECT r.vendor_id, o.name, sum(submissions) s, sum(accepted) a, sum(gold_n) gn, sum(gold_correct) gc,
                   sum(compared) cmp, sum(agreed) agr, sum(rejected) rej, sum(cost) cost,
                   sum(turnaround_s) ta, sum(time_on_task_ms) tot, max(computed_at) computed_at,
                   (SELECT sum(w.gold_correct) FROM scorecard_rows w WHERE w.project_id = r.project_id AND w.vendor_id = r.vendor_id
                      AND w.metric_version = r.metric_version AND w.worker_id IS NOT NULL) AS own_gc,
                   (SELECT count(*) FROM vendor_reviews vr JOIN submissions x ON x.id = vr.submission_id
                     WHERE x.project_id = r.project_id AND x.vendor_id = r.vendor_id) AS reviewed,
                   (SELECT count(*) FROM vendor_reviews vr JOIN submissions x ON x.id = vr.submission_id
                     WHERE x.project_id = r.project_id AND x.vendor_id = r.vendor_id AND vr.decision = 'corrected') AS corrected
            FROM scorecard_rows r JOIN orgs o ON o.id = r.vendor_id
            WHERE r.project_id = :p AND r.metric_version = :m AND r.worker_id IS NULL
            GROUP BY r.project_id, r.metric_version, r.vendor_id, o.name ORDER BY o.name
            """
        ),
        {"p": project.id, "m": METRIC_VERSION},
    )
    vendors, computed = [], None
    for r in rows:
        computed = max(computed, r.computed_at) if computed else r.computed_at
        vendors.append(VendorComparison(
            vendor_id=r.vendor_id, vendor_name=r.name, submissions=r.s, accepted=r.a, acceptance_rate=rate(r.a, r.s - r.gn),
            gold_n=r.gn, gold_accuracy=rate(r.gc, r.gn), agreement=rate(r.agr, r.cmp), rejected=r.rej, cost=float(r.cost),
            cost_per_accepted=rate(float(r.cost), r.a), avg_turnaround_minutes=rate(r.ta / 60, r.s), avg_time_on_task_s=rate(r.tot / 1000, r.s),
            workers_gold_accuracy=rate(r.own_gc or 0, r.gn), vendor_reviewed=r.reviewed, vendor_corrected=r.corrected,
        ))
    return ComparisonOut(metric_version=METRIC_VERSION, computed_at=computed, vendors=vendors)


async def vendor_in_reach(vendor_id: uuid.UUID, user: User, session: AsyncSession) -> Org:
    vendor = await session.get(Org, vendor_id)
    allowed = is_internal(user) and user.role in (Role.DATA_OPS, Role.RESEARCHER) or (
        user.role == Role.VENDOR_ADMIN and user.org_id == vendor_id
    )
    if vendor is None or vendor.kind != OrgKind.VENDOR or not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return vendor


@router.get("/vendors/{vendor_id}/scorecard")
async def vendor_scorecard(
    vendor_id: uuid.UUID,
    project_id: uuid.UUID = Query(),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ScorecardOut:
    """VP3 for Anthropic; VP4 for the vendor itself, with gold numbers held back per PRD PD2."""
    vendor = await vendor_in_reach(vendor_id, user, session)
    await get_visible_project(project_id, user, session)
    rows = await session.execute(
        text(
            """
            SELECT period_start, submissions, gold_n, gold_correct, compared, agreed, rejected, reviewed, accepted, cost
            FROM scorecard_rows
            WHERE project_id = :p AND vendor_id = :v AND metric_version = :m AND worker_id IS NULL
            ORDER BY period_start
            """
        ),
        {"p": project_id, "v": vendor.id, "m": METRIC_VERSION},
    )
    points = [Point(**{k: (float(v) if k == "cost" else v) for k, v in r._mapping.items()}) for r in rows]
    internal = is_internal(user)
    cutoff = None if internal else gold_cutoff()
    period = lambda p: p.period_start  # noqa: E731

    def shown(p: Point) -> PointOut:
        gold_visible = internal or period(p) + _bucket() <= cutoff
        return PointOut(
            period_start=p.period_start, submissions=p.submissions, accepted=p.accepted,
            gold_n=p.gold_n if gold_visible else None, gold_accuracy=p.gold_accuracy if gold_visible else None,
            agreement=p.agreement, cost_per_accepted=rate(p.cost, p.accepted),
        )

    return ScorecardOut(
        metric_version=METRIC_VERSION, vendor_name=vendor.name, gold_shown_until=cutoff,
        points=[shown(p) for p in points], alerts=drop_alerts(points) if internal else [],
    )


def _bucket() -> timedelta:
    return timedelta(minutes=settings.scorecard_bucket_minutes)


@router.get("/vendors/{vendor_id}/workers")
async def vendor_workers(
    vendor_id: uuid.UUID,
    project_id: uuid.UUID = Query(),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[WorkerRow]:
    """Worker quality within a vendor (VP4, C4.4). Agreement and rejections are live; gold
    accuracy appears only after enough gold results, and only up to the last refresh (PD2)."""
    vendor = await vendor_in_reach(vendor_id, user, session)
    await get_visible_project(project_id, user, session)
    internal = is_internal(user)
    cutoff = None if internal else gold_cutoff()
    rows = await session.execute(
        text(
            f"""
            SELECT worker_id, count(*) s,
                   count(*) FILTER (WHERE compared) cmp, count(*) FILTER (WHERE compared AND agrees) agr,
                   count(*) FILTER (WHERE rejected) rej,
                   count(*) FILTER (WHERE is_gold AND (CAST(:cutoff AS timestamptz) IS NULL OR submitted_at < :cutoff)) gn,
                   count(*) FILTER (WHERE gold_correct AND (CAST(:cutoff AS timestamptz) IS NULL OR submitted_at < :cutoff)) gc
            FROM ({SUBMISSION_FACTS}) f
            WHERE vendor_id = :v
            GROUP BY worker_id
            """
        ),
        {"project_id": project_id, "v": vendor.id, "cutoff": cutoff},
    )
    stats = {r.worker_id: r for r in rows}
    people = {u.id: u for u in (await session.scalars(select(User).where(User.id.in_(stats))))}
    out = []
    for worker_id, r in stats.items():
        enough = r.gn >= MIN_GOLD_FOR_WORKER_ACCURACY
        show_gold = internal or enough
        member = people[worker_id]
        out.append(WorkerRow(
            worker_id=worker_id, handle=member.handle,
            display_name=member.display_name if shows_identity(user, member) else None,
            submissions=r.s, agreement=rate(r.agr, r.cmp), rejected=r.rej,
            gold_accuracy=rate(r.gc, r.gn) if show_gold else None, gold_n=r.gn if show_gold else None,
        ))
    return sorted(out, key=lambda w: (w.gold_accuracy is None, w.gold_accuracy or 0))


class EnvironmentWork(BaseModel):
    vendor_id: uuid.UUID
    vendor_name: str
    submitted: int
    checked: int
    passed_checks: int
    reviewed: int
    approved: int
    sent_back: int
    quarantined: int
    check_pass_rate: float | None
    approval_rate: float | None
    # Training outcomes (TO7): what happened when training used the vendor's versions.
    episodes: int
    solved: int
    flagged: int
    recalls: int
    solve_rate: float | None
    flagged_share: float | None


ENVIRONMENT_WORK = """
WITH v AS (
    SELECT ev.id, ev.status, e.org_id,
           EXISTS (SELECT 1 FROM validation_runs r WHERE r.version_id = ev.id) AS checked,
           EXISTS (SELECT 1 FROM validation_runs r WHERE r.version_id = ev.id)
             AND NOT EXISTS (SELECT 1 FROM validation_runs r WHERE r.version_id = ev.id AND NOT r.passed) AS passed_checks,
           (SELECT decision FROM environment_reviews w WHERE w.version_id = ev.id ORDER BY w.created_at DESC LIMIT 1) AS decision
    FROM environment_versions ev JOIN environments e ON e.id = ev.environment_id
    WHERE ev.status <> 'draft' AND (CAST(:p AS uuid) IS NULL OR e.project_id = CAST(:p AS uuid))
),
trained AS (SELECT version_id, sum(episodes) AS episodes, sum(passed) AS solved FROM training_stats GROUP BY version_id),
flagged AS (
    -- each finding carries a running total of flagged attempts, so take the largest per run
    SELECT version_id, sum(n) AS flagged
    FROM (SELECT version_id, run_id, max((evidence->>'flagged_episodes')::int) AS n FROM hack_findings GROUP BY 1, 2) per_run
    GROUP BY version_id
),
recalled AS (SELECT version_id, count(*) AS recalls FROM recalls GROUP BY version_id)
SELECT o.id AS vendor_id, o.name AS vendor_name, count(v.id) AS submitted,
       count(v.id) FILTER (WHERE v.checked) AS checked, count(v.id) FILTER (WHERE v.passed_checks) AS passed_checks,
       count(v.id) FILTER (WHERE v.decision IS NOT NULL) AS reviewed, count(v.id) FILTER (WHERE v.decision = 'approve') AS approved,
       count(v.id) FILTER (WHERE v.decision = 'send_back') AS sent_back, count(v.id) FILTER (WHERE v.status = 'quarantined') AS quarantined,
       coalesce(sum(t.episodes), 0)::bigint AS episodes, coalesce(sum(t.solved), 0)::bigint AS solved,
       coalesce(sum(f.flagged), 0)::bigint AS flagged, coalesce(sum(rc.recalls), 0)::bigint AS recalls
FROM orgs o JOIN v ON v.org_id = o.id
LEFT JOIN trained t ON t.version_id = v.id LEFT JOIN flagged f ON f.version_id = v.id LEFT JOIN recalled rc ON rc.version_id = v.id
GROUP BY o.id, o.name ORDER BY o.name
"""


@router.get("/vendors/environment-work")
async def environment_work(
    project_id: uuid.UUID | None = None, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[EnvironmentWork]:
    """Environment work on the vendor scorecard (VP3): how often a vendor's versions pass the
    sandbox's checks and the researcher's review, how many were later quarantined, and how they
    did in training (TO7): episodes, solve rate, attempts the hack detector flagged, and recalls.
    Computed the same way for every vendor (N16); a vendor sees only its own row."""
    out = []
    for r in await session.execute(text(ENVIRONMENT_WORK), {"p": project_id}):
        if not is_internal(user) and r.vendor_id != user.org_id:
            continue
        out.append(EnvironmentWork(**r._mapping, check_pass_rate=r.passed_checks / r.checked if r.checked else None,
                                   approval_rate=r.approved / r.reviewed if r.reviewed else None,
                                   solve_rate=r.solved / r.episodes if r.episodes else None,
                                   flagged_share=r.flagged / r.episodes if r.episodes else None))
    return out


class CaseHandling(BaseModel):
    vendor_id: uuid.UUID
    vendor_name: str
    opened: int
    decided: int
    cleared: int
    suspended_or_removed: int
    reinstated: int
    open_now: int
    open_over_3_days: int
    median_hours_to_decide: float | None


CASE_HANDLING = """
SELECT o.id AS vendor_id, o.name AS vendor_name, count(c.id) AS opened,
       count(c.id) FILTER (WHERE c.status = 'decided') AS decided,
       count(c.id) FILTER (WHERE c.decision = 'clear') AS cleared,
       count(c.id) FILTER (WHERE c.decision IN ('suspend', 'remove')) AS suspended_or_removed,
       count(c.id) FILTER (WHERE EXISTS (SELECT 1 FROM case_actions a WHERE a.case_id = c.id AND a.action = 'reverse')) AS reinstated,
       count(c.id) FILTER (WHERE c.status = 'open') AS open_now,
       count(c.id) FILTER (WHERE c.status = 'open' AND c.opened_at < :stale) AS open_over_3_days,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM c.decided_at - c.opened_at) / 3600)
         FILTER (WHERE c.decided_at IS NOT NULL) AS median_hours_to_decide
FROM orgs o LEFT JOIN cases c ON c.vendor_id = o.id AND c.subject_type = 'worker'
WHERE o.kind = 'vendor'
GROUP BY o.id, o.name ORDER BY o.name
"""


@router.get("/vendors/case-handling")
async def case_handling(user: User = Depends(require_roles(Role.DATA_OPS)),
                        session: AsyncSession = Depends(get_session)) -> list[CaseHandling]:
    """How each vendor resolves its own workers' cases (AB7), computed the same way for every vendor."""
    rows = await session.execute(text(CASE_HANDLING), {"stale": clock.now() - timedelta(days=3)})
    return [CaseHandling(**r._mapping) for r in rows]
