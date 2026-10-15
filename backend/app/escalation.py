"""Discreet escalation (AB14, D26, D32): measure a worker more closely before anyone decides.

A watch finding (one signal on its own) opens an escalation. While it's open, the worker's
next tasks are drawn from gold more often: gold they haven't seen, routed to their vendor
like any other task, so nothing about the tasks themselves changes. Their integrity grade is
"watch", so the release stage asks for more later gold before verifying their answers.

The escalation ends on evidence. Once the worker has answered CLEARS_WATCH gold tasks after
the finding, it's cleared if their accuracy on those meets the project's bar, and confirmed
only if the evidence clearly says they're below it: the top of the 95% interval (the same
Wilson interval as the quality grade, D30) is under the bar. Being merely unlucky on ten
answers isn't enough, and low quality on its own is never abuse (PRD PD4). Anything in
between keeps the worker watched, until GIVE_UP_AFTER later gold answers settle nothing and
the escalation is cleared as inconclusive. Confirming opens a case for the vendor admin like
any case-severity finding; a case opened another way ends the escalation too. Nothing in this module is visible to the worker or the
vendor: no notice, no audit event in the vendor's log, no change to the worker's tasks
beyond which of them come first.
"""

import random
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, detection_quality
from app.abuse import Observation, open_or_join_case
from app.audit import record

CLEARS_WATCH = 10  # later gold answers before a watch finding can be cleared or confirmed (D32)
GIVE_UP_AFTER = 30  # later gold answers after which an unsettled watch is cleared as inconclusive
ESCALATED_GOLD_SHARE = 0.5  # chance that an escalated worker's next claim tries gold first

# Gold answered after each open escalation began, and how much of it was right.
PROGRESS = """
SELECT x.id, x.worker_id, x.project_id, x.started_at, u.org_id AS vendor_id, p.quality_bar,
       count(g.task_id) AS gold_n,
       count(g.task_id) FILTER (WHERE s.answer @> g.answer) AS gold_correct
FROM escalations x
JOIN users u ON u.id = x.worker_id
JOIN projects p ON p.id = x.project_id
LEFT JOIN submission_events e ON e.worker_id = x.worker_id AND e.project_id = x.project_id AND e.id > x.after_event
LEFT JOIN submissions s ON s.id = e.submission_id
LEFT JOIN gold_answers g ON g.task_id = s.task_id
WHERE x.ended_at IS NULL {where}
GROUP BY x.id, u.org_id, p.quality_bar
"""


async def start(session: AsyncSession, *, finding_id: uuid.UUID, worker_id: uuid.UUID, project_id: uuid.UUID, after_event: int) -> None:
    opened = await session.scalar(
        text(
            "INSERT INTO escalations (worker_id, project_id, finding_id, after_event, started_at) "
            "VALUES (:w, :p, :f, :e, :now) ON CONFLICT (worker_id, project_id) WHERE ended_at IS NULL DO NOTHING RETURNING id"
        ),
        {"w": worker_id, "p": project_id, "f": finding_id, "e": after_event, "now": clock.now()},
    )
    if opened:
        # Anthropic's log only (no org): the vendor must not learn the worker is being watched.
        record(session, actor=None, action="escalation.started", entity_type="escalation", entity_id=opened, org_id=None,
               after={"worker_id": str(worker_id), "project_id": str(project_id), "finding_id": str(finding_id)})


async def end_for_case(session: AsyncSession, worker_id: uuid.UUID, case_id: uuid.UUID) -> None:
    await session.execute(
        text("UPDATE escalations SET ended_at = :now, outcome = 'case', case_id = :c WHERE worker_id = :w AND ended_at IS NULL"),
        {"now": clock.now(), "c": case_id, "w": worker_id},
    )


async def progress(session: AsyncSession, project_id: uuid.UUID) -> dict[uuid.UUID, tuple[int, int]]:
    rows = await session.execute(text(PROGRESS.format(where="AND x.project_id = :p")), {"p": project_id})
    return {r.worker_id: (r.gold_n, r.gold_correct) for r in rows}


async def is_escalated(session: AsyncSession, worker_id: uuid.UUID, project_id: uuid.UUID | None) -> bool:
    return bool(await session.scalar(
        text("SELECT 1 FROM escalations WHERE worker_id = :w AND ended_at IS NULL "
             "AND (CAST(:p AS uuid) IS NULL OR project_id = CAST(:p AS uuid)) LIMIT 1"),
        {"w": worker_id, "p": project_id},
    ))


def prefer_gold() -> bool:
    return random.random() < ESCALATED_GOLD_SHARE


async def review(session: AsyncSession) -> dict[str, int]:
    """End every open escalation whose later gold settles it: cleared, or confirmed as a case."""
    from app.grades import wilson  # app.grades reads escalations, so import here
    counts = {"cleared": 0, "confirmed": 0}
    for r in (await session.execute(text(PROGRESS.format(where="")))).all():
        if r.gold_n < CLEARS_WATCH:
            continue
        now = clock.now()
        bar = float(r.quality_bar)
        clearly_below = wilson(r.gold_correct, r.gold_n)[1] < bar
        if not clearly_below and r.gold_correct < bar * r.gold_n and r.gold_n < GIVE_UP_AFTER:
            continue  # below the bar, but not clearly: keep measuring
        if not clearly_below:
            await session.execute(text("UPDATE escalations SET ended_at = :now, outcome = 'cleared' WHERE id = :x"),
                                  {"now": now, "x": r.id})
            record(session, actor=None, action="escalation.cleared", entity_type="escalation", entity_id=r.id, org_id=None,
                   after={"gold_n": r.gold_n, "gold_correct": r.gold_correct})
            await detection_quality.record_cleared(session, r.worker_id, r.project_id)
            counts["cleared"] += 1
            continue
        # The vendor admin sees what was observed, never that it came from test questions (N17).
        observation = Observation(
            "escalation", "escalation-v1",
            f"Since {r.started_at:%d %b %H:%M}, the accuracy of this worker's answers has been below the project's quality bar.",
            {},
        )
        case_id, _ = await open_or_join_case(session, worker_id=r.worker_id, vendor_id=r.vendor_id,
                                             project_id=r.project_id, findings=[observation])
        await session.execute(text("UPDATE escalations SET ended_at = :now, outcome = 'confirmed', case_id = :c WHERE id = :x"),
                              {"now": now, "c": case_id, "x": r.id})
        record(session, actor=None, action="escalation.confirmed", entity_type="escalation", entity_id=r.id, org_id=None,
               after={"gold_n": r.gold_n, "gold_correct": r.gold_correct, "case_id": str(case_id)})
        counts["confirmed"] += 1
    await session.commit()
    return counts
