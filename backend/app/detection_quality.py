"""Measuring detection, and letting new rules prove themselves first (AB15, AB16, D27, D28,
D40, D41).

- Modes: every rule version the platform hasn't seen before is in shadow. Its findings are
  stored, and its flagged answers go to reviewers, but nothing else happens: no case, no
  escalation, no hold. Data ops promotes a version once enough of its flagged answers have been
  reviewed and its precision meets their bar.
- Review requests: a random share of regular answers (the audit sample) and a few answers from
  each finding join the spot-check queue. Reviewers can't tell them from other spot checks.
- Outcomes: reviewers' abuse judgements and cleared escalations go back to the detection
  service as labels.
- Measures: a rule version's precision is the share of its reviewed flagged answers judged
  abusive; for the hack detector, the share of its transcript checks that ended in a flag.
  The miss rate is the share of audited answers, from workers no live rule has flagged on that
  project, that reviewers judged abusive.
"""

import random
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.config import settings

PER_FINDING = 3  # flagged answers from each finding sent for review
MIN_REVIEWED = 20  # reviewed flagged answers a version needs before it can be promoted (D41)
DEFAULT_BAR = 0.8  # the precision bar offered when data ops hasn't set one for a version


async def mode(session: AsyncSession, detector: str, rule_version: str) -> str:
    """The version's mode; a version seen for the first time is recorded in shadow (D27)."""
    await session.execute(
        text("INSERT INTO rule_modes (rule_version, detector, mode, changed_at) VALUES (:v, :d, 'shadow', :now) "
             "ON CONFLICT (rule_version) DO NOTHING"),
        {"v": rule_version, "d": detector, "now": clock.now()},
    )
    return await session.scalar(text("SELECT mode FROM rule_modes WHERE rule_version = :v"), {"v": rule_version})


async def modes(session: AsyncSession, detector: str) -> dict[str, str]:
    rows = await session.execute(text("SELECT rule_version, mode FROM rule_modes WHERE detector = :d"), {"d": detector})
    return {r.rule_version: r.mode for r in rows}


async def request_reviews(session: AsyncSession, finding_id: uuid.UUID, submission_ids: list[str]) -> None:
    """Send a few of a finding's answers to spot checks: AB14's extra review, and how a rule's
    precision is measured. Gold answers are never sent: their correct answer is already known."""
    await session.execute(
        text(
            """
            INSERT INTO review_requests (submission_id, reason, finding_id, created_at)
            SELECT s.id, 'finding', :f, :now FROM submissions s
            WHERE s.id = ANY(CAST(:ids AS uuid[])) AND NOT EXISTS (SELECT 1 FROM gold_answers g WHERE g.task_id = s.task_id)
            ORDER BY s.submitted_at DESC LIMIT :n
            ON CONFLICT (submission_id) DO NOTHING
            """
        ),
        {"f": finding_id, "ids": submission_ids, "now": clock.now(), "n": PER_FINDING},
    )


async def maybe_audit(session: AsyncSession, submission_id: uuid.UUID, task_id: uuid.UUID) -> None:
    """The audit sample (AB15, D28): a random share of regular answers, chosen at submission."""
    if random.random() >= settings.audit_rate:
        return
    if await session.scalar(text("SELECT 1 FROM gold_answers WHERE task_id = :t"), {"t": task_id}):
        return
    await session.execute(
        text("INSERT INTO review_requests (submission_id, reason, created_at) VALUES (:s, 'audit', :now) ON CONFLICT DO NOTHING"),
        {"s": submission_id, "now": clock.now()},
    )


async def record_judgement(session: AsyncSession, submission_id: uuid.UUID, looks_abusive: bool) -> None:
    await session.execute(
        text(
            "INSERT INTO detection_outcomes (kind, worker_id, project_id, submission_id, outcome, through_event, created_at) "
            "SELECT 'judgement', s.worker_id, s.project_id, s.id, :o, "
            "coalesce((SELECT max(id) FROM submission_events), 0), :now FROM submissions s WHERE s.id = :s"
        ),
        {"s": submission_id, "o": "abusive" if looks_abusive else "not_abusive", "now": clock.now()},
    )


async def record_cleared(session: AsyncSession, worker_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """A cleared escalation is a label like a case decision: judge the worker afresh from here."""
    await session.execute(
        text("INSERT INTO detection_outcomes (kind, worker_id, project_id, outcome, through_event, created_at) "
             "VALUES ('escalation', :w, :p, 'cleared', coalesce((SELECT max(id) FROM submission_events), 0), :now)"),
        {"w": worker_id, "p": project_id, "now": clock.now()},
    )


# Per rule version: findings, flagged answers reviewed, and how many reviewers judged abusive.
RULE_MEASURES = """
WITH judged AS (
    SELECT DISTINCT ON (submission_id) submission_id, looks_abusive FROM reviews
    WHERE looks_abusive IS NOT NULL ORDER BY submission_id, created_at DESC, id
),
flagged AS (
    SELECT DISTINCT f.rule_version, CAST(sid AS uuid) AS submission_id
    FROM findings f, jsonb_array_elements_text(f.evidence->'submission_ids') AS sid
),
worker_rules AS (
    SELECT fl.rule_version, count(j.submission_id) AS reviewed, count(j.submission_id) FILTER (WHERE j.looks_abusive) AS abusive
    FROM flagged fl LEFT JOIN judged j ON j.submission_id = fl.submission_id GROUP BY fl.rule_version
),
hack_rules AS (
    SELECT f.rule_version, count(c.outcome) AS reviewed, count(c.outcome) FILTER (WHERE c.outcome = 'flagged') AS abusive
    FROM hack_findings f LEFT JOIN hack_checks c ON c.finding_id = f.id GROUP BY f.rule_version
),
counts AS (
    SELECT rule_version, count(*) AS findings, count(DISTINCT worker_id) AS subjects FROM findings GROUP BY rule_version
    UNION ALL
    SELECT rule_version, count(*), count(DISTINCT version_id) FROM hack_findings GROUP BY rule_version
)
SELECT m.rule_version, m.detector, m.mode, m.bar, m.changed_at, coalesce(c.findings, 0) AS findings,
       coalesce(c.subjects, 0) AS subjects,
       coalesce(w.reviewed, h.reviewed, 0) AS reviewed, coalesce(w.abusive, h.abusive, 0) AS abusive
FROM rule_modes m
LEFT JOIN counts c ON c.rule_version = m.rule_version
LEFT JOIN worker_rules w ON w.rule_version = m.rule_version
LEFT JOIN hack_rules h ON h.rule_version = m.rule_version
"""

# Audited answers from workers no live rule has flagged on that project, and how many looked abusive.
MISSES = """
WITH judged AS (
    SELECT DISTINCT ON (submission_id) submission_id, looks_abusive FROM reviews
    WHERE looks_abusive IS NOT NULL ORDER BY submission_id, created_at DESC, id
)
SELECT count(*) AS audited, count(*) FILTER (WHERE j.looks_abusive) AS abusive
FROM review_requests q
JOIN judged j ON j.submission_id = q.submission_id
JOIN submissions s ON s.id = q.submission_id
WHERE q.reason = 'audit'
  AND NOT EXISTS (SELECT 1 FROM findings f WHERE f.worker_id = s.worker_id AND f.project_id = s.project_id AND NOT f.shadow)
"""


async def rule_measures(session: AsyncSession, rule_version: str | None = None) -> list:
    where = " WHERE m.rule_version = :v" if rule_version else ""
    sql = RULE_MEASURES + where + " ORDER BY m.detector DESC, m.mode, m.rule_version"
    return (await session.execute(text(sql), {"v": rule_version} if rule_version else {})).all()


async def misses(session: AsyncSession):
    return (await session.execute(text(MISSES))).one()
