"""Scorecard roll-ups (VP2-VP4, D11), computed by the platform from its own records (N16).

Every row records the metric version that produced it (N19). Cost follows each answer's
contract: per-task contracts pay for every submitted answer, hourly contracts pay for
platform-measured time on task (PRD PD3). Cost per accepted unit = cost / accepted answers.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.config import settings
from app.quality import METRIC_VERSION, SUBMISSION_FACTS

ROLLUP = f"""
INSERT INTO scorecard_rows (metric_version, project_id, vendor_id, worker_id, period_start, submissions, gold_n,
    gold_correct, compared, agreed, reviewed, rejected, accepted, time_on_task_ms, turnaround_s, cost, computed_at)
SELECT :metric_version, :project_id, f.vendor_id, {{worker}},
       date_bin(make_interval(mins => :bucket), f.submitted_at, TIMESTAMPTZ '2000-01-01') AS period_start,
       count(*), count(*) FILTER (WHERE f.is_gold), count(*) FILTER (WHERE f.gold_correct),
       count(*) FILTER (WHERE f.compared), count(*) FILTER (WHERE f.compared AND f.agrees),
       count(*) FILTER (WHERE f.reviewed), count(*) FILTER (WHERE f.rejected), count(*) FILTER (WHERE f.accepted),
       sum(f.time_on_task_ms),
       sum(extract(epoch FROM f.submitted_at - r.routed_at))::bigint,
       sum(CASE c.price_unit WHEN 'task' THEN c.price
                             WHEN 'hour' THEN c.price * f.time_on_task_ms / 3600000.0
                             ELSE 0 END),
       :now
FROM ({SUBMISSION_FACTS}) f
JOIN submissions s ON s.id = f.id
JOIN contracts c ON c.id = s.contract_id
JOIN task_routes r ON r.task_id = s.task_id AND r.vendor_id = s.vendor_id
GROUP BY f.vendor_id, 4, 5
"""


async def rollup_project(session: AsyncSession, project_id: uuid.UUID) -> int:
    """Recompute every scorecard row for one project, vendor-level and worker-level."""
    await session.execute(
        text("DELETE FROM scorecard_rows WHERE project_id = :p AND metric_version = :m"), {"p": project_id, "m": METRIC_VERSION}
    )
    params = {"project_id": project_id, "metric_version": METRIC_VERSION, "bucket": settings.scorecard_bucket_minutes, "now": clock.now()}
    vendor = await session.execute(text(ROLLUP.replace("{worker}", "CAST(NULL AS uuid)")), params)
    worker = await session.execute(text(ROLLUP.replace("{worker}", "f.worker_id")), params)
    return vendor.rowcount + worker.rowcount


def gold_cutoff() -> datetime:
    """Vendors see gold-based numbers only up to the last refresh boundary (PRD PD2)."""
    step = timedelta(minutes=settings.vendor_gold_refresh_minutes)
    now = clock.now()
    epoch = datetime(2000, 1, 1, tzinfo=now.tzinfo)
    return epoch + ((now - epoch) // step) * step


@dataclass
class Point:
    period_start: datetime
    submissions: int
    gold_n: int
    gold_correct: int
    compared: int
    agreed: int
    rejected: int
    reviewed: int
    accepted: int
    cost: float

    @property
    def gold_accuracy(self) -> float | None:
        return self.gold_correct / self.gold_n if self.gold_n else None

    @property
    def agreement(self) -> float | None:
        return self.agreed / self.compared if self.compared else None


def drop_alerts(points: list[Point], *, window: int = 4, min_gold: int = 30, drop: float = 0.08, z: float = 3.0) -> list[dict]:
    """Flag periods where gold accuracy over the last `window` periods falls at least `drop` below
    the vendor's accuracy over everything before that window, by at least `z` standard errors of
    the recent window (VP3). Both conditions matter: the size of the drop, and that it isn't noise."""
    alerts = []
    for i in range(window, len(points)):
        recent = points[i - window + 1 : i + 1]
        before = points[: i - window + 1]
        r_n, b_n = sum(p.gold_n for p in recent), sum(p.gold_n for p in before)
        if r_n < min_gold or b_n < min_gold:
            continue
        r_acc = sum(p.gold_correct for p in recent) / r_n
        b_acc = sum(p.gold_correct for p in before) / b_n
        se = (b_acc * (1 - b_acc) / r_n) ** 0.5
        if b_acc - r_acc >= drop and (se == 0 or (b_acc - r_acc) / se >= z):
            alerts.append({"period_start": points[i].period_start, "metric": "gold_accuracy",
                           "recent": round(r_acc, 3), "baseline": round(b_acc, 3)})
    return alerts
