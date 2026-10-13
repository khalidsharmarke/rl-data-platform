"""Quality measures (DC4, N16), computed by the platform the same way for every vendor.

Metric definition "quality-v1" (N19):
- Gold accuracy: share of a worker's answers on gold tasks whose fields match the gold
  answer (JSON containment, so a free-text rationale doesn't matter).
- Agreement: on regular tasks, share of a worker's answers whose "choice" matches the
  answer of at least one other worker at the same vendor on the same task, out of answers
  that had another answer to compare with.
- Rejected: the latest researcher review of the submission is a rejection.
- Accepted: a regular-task answer that isn't rejected, held (an open abuse case, AB8) or
  removed in a clean-up (AB6),
  whose worker is not below the project's quality bar (gold accuracy), once the worker has
  enough gold results to judge.
"""

import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

METRIC_VERSION = "quality-v1"

# One row per submission with everything the measures need. Later milestones add exclusions
# (held and removed submissions) here, so every measure stays consistent.
SUBMISSION_FACTS = """
WITH s AS (
    SELECT s.*, (g.task_id IS NOT NULL) AS is_gold,
           CASE WHEN g.task_id IS NULL THEN NULL ELSE (s.answer @> g.answer) END AS gold_correct
    FROM submissions s
    LEFT JOIN gold_answers g ON g.task_id = s.task_id
    WHERE s.project_id = :project_id
),
latest_review AS (
    SELECT DISTINCT ON (submission_id) submission_id, verdict
    FROM reviews ORDER BY submission_id, created_at DESC, id
),
agreement AS (
    SELECT s.id,
           count(o.id) > 0 AS compared,
           bool_or(o.answer->'choice' = s.answer->'choice') AS agrees
    FROM s
    LEFT JOIN submissions o
      ON o.task_id = s.task_id AND o.vendor_id = s.vendor_id AND o.id <> s.id
     AND NOT EXISTS (SELECT 1 FROM removals rm WHERE rm.submission_id = o.id)
    WHERE NOT s.is_gold
    GROUP BY s.id
),
worker_gold AS (
    SELECT worker_id, count(*) AS gold_n, count(*) FILTER (WHERE gold_correct) AS gold_correct
    FROM s WHERE is_gold GROUP BY worker_id
),
facts AS (
    SELECT s.id, s.vendor_id, s.worker_id, s.is_gold, s.gold_correct, s.time_on_task_ms, s.submitted_at,
           coalesce(a.compared, false) AS compared, coalesce(a.agrees, false) AS agrees,
           (lr.verdict = 'rejected') IS TRUE AS rejected,
           (lr.verdict IS NOT NULL) AS reviewed,
           coalesce(wg.gold_n, 0) AS worker_gold_n,
           coalesce(wg.gold_correct, 0) AS worker_gold_correct,
           EXISTS (SELECT 1 FROM holds h WHERE h.worker_id = s.worker_id AND h.released_at IS NULL) AS held,
           EXISTS (SELECT 1 FROM removals rm WHERE rm.submission_id = s.id) AS removed
    FROM s
    LEFT JOIN agreement a ON a.id = s.id
    LEFT JOIN latest_review lr ON lr.submission_id = s.id
    LEFT JOIN worker_gold wg ON wg.worker_id = s.worker_id
)
SELECT f.*,
       (NOT f.is_gold AND NOT f.rejected AND NOT f.held AND NOT f.removed AND (
            f.worker_gold_n < p.min_gold_results
            OR f.worker_gold_correct::numeric / f.worker_gold_n >= p.quality_bar
       )) AS accepted
FROM facts f, projects p WHERE p.id = :project_id
"""


@dataclass
class Measures:
    submissions: int
    gold_n: int
    gold_correct: int
    compared: int
    agreed: int
    reviewed: int
    rejected: int
    accepted: int

    @property
    def gold_accuracy(self) -> float | None:
        return self.gold_correct / self.gold_n if self.gold_n else None

    @property
    def agreement(self) -> float | None:
        return self.agreed / self.compared if self.compared else None

    def as_dict(self) -> dict:
        return {**asdict(self), "gold_accuracy": self.gold_accuracy, "agreement": self.agreement}


AGGREGATE = """
SELECT {group} AS key,
       count(*) AS submissions,
       count(*) FILTER (WHERE is_gold) AS gold_n,
       count(*) FILTER (WHERE gold_correct) AS gold_correct,
       count(*) FILTER (WHERE compared) AS compared,
       count(*) FILTER (WHERE compared AND agrees) AS agreed,
       count(*) FILTER (WHERE reviewed) AS reviewed,
       count(*) FILTER (WHERE rejected) AS rejected,
       count(*) FILTER (WHERE accepted) AS accepted
FROM ({facts}) x
GROUP BY {group}
"""


async def measures_by(session: AsyncSession, project_id: uuid.UUID, group: str) -> dict[uuid.UUID, Measures]:
    assert group in ("vendor_id", "worker_id")
    rows = await session.execute(text(AGGREGATE.format(group=group, facts=SUBMISSION_FACTS)), {"project_id": project_id})
    return {r.key: Measures(**{k: getattr(r, k) for k in Measures.__dataclass_fields__}) for r in rows}
