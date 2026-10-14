"""The release stage (DC10, PD7, D25, D29, D31): when an accepted answer may reach training.

Release is computed from current evidence each time it's asked for, never stored, so a new
hold or newly failed gold takes effect at the next export. An accepted answer (quality-v1)
is released when:

  Integrity, always required (a hard gate)
  - the detection service has processed its event (D25)
  - it isn't held or removed (already part of "accepted")
  - the project's minimum gap has passed since it was submitted

  Evidence, required for "verified"
  - its task has all its overlapping answers at the worker's vendor
  - the worker has answered enough gold *after* it, with accuracy at or above the quality
    bar. How much is enough depends on the worker's integrity grade (app/grades.py): trust
    shortens the wait, never to nothing. Evidence from the answer's own period is what catches a worker who changes: their
    earlier answers stop being released as soon as their later gold falls.

An answer that meets the integrity conditions but not the evidence ones is released as
"provisional" once it has waited the project's cap, so slow-to-prove workers aren't
silently dropped (D31). Everything else waits.
"""

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, grades
from app.models import Project
from app.quality import SUBMISSION_FACTS

FACTS = f"""
WITH f AS ({SUBMISSION_FACTS}),
ordered AS (
    SELECT f.*, e.id AS event_id, s.task_id,
           count(*) FILTER (WHERE f.is_gold AND NOT f.removed) OVER later AS later_gold_n,
           count(*) FILTER (WHERE f.is_gold AND NOT f.removed AND f.gold_correct) OVER later AS later_gold_correct
    FROM f
    JOIN submissions s ON s.id = f.id
    JOIN submission_events e ON e.submission_id = f.id
    WINDOW later AS (PARTITION BY f.worker_id ORDER BY f.submitted_at, f.id ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING)
),
answers_per_task AS (
    SELECT s.task_id, s.vendor_id, count(*) AS n
    FROM submissions s
    WHERE s.project_id = :project_id AND NOT EXISTS (SELECT 1 FROM removals rm WHERE rm.submission_id = s.id)
    GROUP BY s.task_id, s.vendor_id
)
SELECT o.id, o.worker_id, o.vendor_id, o.submitted_at, o.event_id, o.later_gold_n, o.later_gold_correct,
       coalesce(a.n, 0) AS task_answers
FROM ordered o
LEFT JOIN answers_per_task a ON a.task_id = o.task_id AND a.vendor_id = o.vendor_id
WHERE o.accepted
ORDER BY o.submitted_at, o.id
"""


@dataclass
class Decision:
    submission_id: uuid.UUID
    worker_id: uuid.UUID
    state: str  # verified, provisional, or waiting
    waiting_for: str | None  # detection, gap or evidence


@dataclass
class ReleaseView:
    decisions: list[Decision]
    detection_through: int
    as_of: datetime
    grades: dict[uuid.UUID, grades.Grade]

    def released(self) -> list[Decision]:
        return [d for d in self.decisions if d.state != "waiting"]

    def summary(self) -> dict:
        states = Counter(d.state for d in self.decisions)
        waiting = Counter(d.waiting_for for d in self.decisions if d.state == "waiting")
        return {
            "accepted": len(self.decisions), "verified": states["verified"], "provisional": states["provisional"],
            "waiting": {"detection": waiting["detection"], "gap": waiting["gap"], "evidence": waiting["evidence"]},
            "detection_through": self.detection_through, "as_of": self.as_of,
        }


async def evaluate(session: AsyncSession, project: Project) -> ReleaseView:
    now = clock.now()
    through = await session.scalar(text("SELECT coalesce(max(processed_through), 0) FROM detection_progress")) or 0
    gap = timedelta(minutes=project.release_after_minutes)
    cap = timedelta(minutes=project.release_cap_minutes)
    bar = float(project.quality_bar)
    graded = await grades.compute(session, project)
    decisions = []
    for r in (await session.execute(text(FACTS), {"project_id": project.id})).all():
        age = now - r.submitted_at
        if r.event_id > through:
            state, waiting_for = "waiting", "detection"
        elif age < gap:
            state, waiting_for = "waiting", "gap"
        elif ((needed := graded[r.worker_id].later_gold_needed) is not None
              and r.task_answers >= project.overlap and r.later_gold_n >= needed
              and r.later_gold_correct >= bar * r.later_gold_n):
            state, waiting_for = "verified", None
        elif age >= cap:
            state, waiting_for = "provisional", None
        else:
            state, waiting_for = "waiting", "evidence"
        decisions.append(Decision(r.id, r.worker_id, state, waiting_for))
    return ReleaseView(decisions, through, now, graded)
