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
       count(*), count(*) FILTER (WHERE f.is_gold), count(*) FILTER (WHERE {{gold}}),
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
    # Vendor rows count gold on what the vendor submitted; worker rows on the worker's own answers (D44).
    vendor = await session.execute(text(ROLLUP.replace("{worker}", "CAST(NULL AS uuid)").replace("{gold}", "f.vendor_gold_correct")), params)
    worker = await session.execute(text(ROLLUP.replace("{worker}", "f.worker_id").replace("{gold}", "f.gold_correct")), params)
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


def significant_drop(recent_hits: int, recent_n: int, before_hits: int, before_n: int, *, drop: float, z: float) -> bool:
    """The recent rate is at least `drop` below the earlier one, and the gap is significant by a
    two-proportion z-test, which counts the uncertainty on both sides (D19)."""
    r_rate, b_rate = recent_hits / recent_n, before_hits / before_n
    pooled = (recent_hits + before_hits) / (recent_n + before_n)
    se = (pooled * (1 - pooled) * (1 / recent_n + 1 / before_n)) ** 0.5
    return b_rate - r_rate >= drop and (se == 0 or (b_rate - r_rate) / se >= z)


def drop_alerts(points: list[Point], *, window: int = 4, min_gold: int = 60, drop: float = 0.08, z: float = 2.0) -> list[dict]:
    """Flag periods where gold accuracy over the last `window` periods falls at least `drop` below
    the vendor's accuracy over everything before that window (VP3, D19).

    The drop must also be significant by a two-proportion z-test, which counts the uncertainty
    on both sides: a baseline of one lucky early period is as noisy as a short recent window.
    Each side needs at least `min_gold` gold answers."""
    alerts = []
    for i in range(window, len(points)):
        recent = points[i - window + 1 : i + 1]
        before = points[: i - window + 1]
        r_n, b_n = sum(p.gold_n for p in recent), sum(p.gold_n for p in before)
        if r_n < min_gold or b_n < min_gold:
            continue
        r_hits, b_hits = sum(p.gold_correct for p in recent), sum(p.gold_correct for p in before)
        r_acc, b_acc = r_hits / r_n, b_hits / b_n
        if significant_drop(r_hits, r_n, b_hits, b_n, drop=drop, z=z):
            alerts.append({"period_start": points[i].period_start, "metric": "gold_accuracy",
                           "recent": round(r_acc, 3), "baseline": round(b_acc, 3)})
    return alerts
