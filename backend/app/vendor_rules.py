"""Vendor-private rules (AB9, D56, PRD PD5): a vendor's own thresholds on signals the platform records.

A vendor knows its workforce; it may want to look at a worker sooner than the shared rules would.
Its rules run as a platform job over its own workers' recent answers only. A finding opens a
private case in the vendor's workspace: Anthropic doesn't list it, it never holds answers, never
becomes a detection label, and never counts in scorecards or in how the vendor handles cases.
What the vendor decides on it (say, suspending a worker) has the same effect as any decision
the vendor makes about its own people.
"""

import json
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.abuse import add_action

RECONSIDER_AFTER = timedelta(days=7)  # a decided private case isn't reopened by the same vendor sooner

# Each signal: the statistic over a worker's latest answers, whether the rule fires above or below
# the threshold, and how to say it.
SIGNALS = {
    "fast": ("percentile_cont(0.5) WITHIN GROUP (ORDER BY time_on_task_ms) / 1000.0", "below", "median time on task {v:.1f} s"),
    "pasted": ("avg(CASE WHEN (client_signals->>'pasted')::boolean THEN 1 ELSE 0 END)", "above", "{v:.0%} of answers pasted"),
    "focus_changes": ("avg(coalesce((client_signals->>'focus_changes')::int, 0))", "above", "{v:.1f} focus changes per answer"),
    "quick_start": ("percentile_cont(0.5) WITHIN GROUP (ORDER BY (client_signals->>'first_input_ms')::int) / 1000.0", "below",
                    "median {v:.1f} s before the first input"),
}


async def run(session: AsyncSession) -> int:
    """The job: evaluate every active vendor rule on its vendor's workers. Returns how many cases it opened or joined."""
    found = 0
    rules = (await session.execute(text("SELECT * FROM vendor_rules WHERE retired_at IS NULL"))).all()
    for rule in rules:
        stat, direction, say = SIGNALS[rule.signal]
        rows = await session.execute(text(f"""
            WITH latest AS (
                SELECT s.*, row_number() OVER (PARTITION BY s.worker_id ORDER BY s.submitted_at DESC) AS n
                FROM submissions s JOIN users u ON u.id = s.worker_id
                WHERE s.vendor_id = :v AND u.suspended_at IS NULL
            )
            SELECT worker_id, count(*) AS answers, {stat} AS value, array_agg(id) AS ids
            FROM latest WHERE n <= :w GROUP BY worker_id HAVING count(*) >= :w
        """), {"v": rule.org_id, "w": rule.window})
        for r in rows:
            if r.value is None:
                continue
            value, threshold = float(r.value), float(rule.threshold)
            if (value >= threshold) if direction == "below" else (value <= threshold):
                continue
            recently = await session.scalar(text(
                "SELECT 1 FROM cases c JOIN flags f ON f.case_id = c.id WHERE c.private AND c.subject_id = :w AND f.rule = :r "
                "AND c.status = 'decided' AND c.decided_at > :since LIMIT 1"),
                {"w": r.worker_id, "r": f"vendor:{rule.id}", "since": clock.now() - RECONSIDER_AFTER})
            if recently:
                continue
            found += await open_private(session, rule, r.worker_id,
                                        f"Your rule “{rule.name}”: {say.format(v=value)} over the last {r.answers} answers "
                                        f"(you set {direction} {threshold:g}).", {"submission_ids": [str(i) for i in r.ids[:6]]})
    await session.commit()
    return found


async def open_private(session: AsyncSession, rule, worker_id, observation: str, evidence: dict) -> int:
    now = clock.now()
    case_id = await session.scalar(text("SELECT id FROM cases WHERE private AND subject_id = :w AND status = 'open'"), {"w": worker_id})
    if case_id is None:
        case_id = await session.scalar(text(
            "INSERT INTO cases (subject_type, subject_id, vendor_id, owner, opened_at, private) "
            "VALUES ('worker', :w, :v, 'vendor_admin', :now, true) RETURNING id"), {"w": worker_id, "v": rule.org_id, "now": now})
        await add_action(session, case_id, "opened", None, "Opened by your own rule", now)
    inserted = await session.scalar(text(
        "INSERT INTO flags (case_id, rule, rule_version, observation, evidence, created_at) "
        "VALUES (:c, :r, :rv, :o, CAST(:e AS jsonb), :now) ON CONFLICT ON CONSTRAINT flags_once_per_rule_per_case DO NOTHING RETURNING id"),
        {"c": case_id, "r": f"vendor:{rule.id}", "rv": rule.name, "o": observation, "e": json.dumps(evidence), "now": now})
    return int(inserted is not None)
