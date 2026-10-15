"""Vendor oversight: rules about vendors as a whole, and the vendor cases they open (AB2, AB5,
AB7, D13, D42).

The rules run as a platform job over data the platform already keeps (D15), the same way for
every vendor (N16):

- quality-volume-v1: on a project, the vendor's gold accuracy over its latest periods fell
  significantly below its earlier accuracy (D19's test) while its volume per period rose.
- quarantines-v1: two or more of the vendor's environment versions were quarantined in the
  last 30 days.
- case-handling-v1: how the vendor handles its own workers' cases: most of its decided cases
  cleared, suspended workers reinstated, or cases left open for days.

A finding opens (or joins) a vendor case owned by data ops. The vendor doesn't see it until data
ops has read it and asks the vendor to respond, so a rule's mistake never reaches a vendor
without a person. Actions on the vendor (lower allocation, pause, offboard) wait for the
vendor's response, or for the response window to pass.

Vendors read what was observed, never the rules or thresholds (N17). Rules that need
identities across vendors (removed workers returning, matching answers) wait for AB10.
"""

import json
import uuid
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.abuse import add_action
from app.audit import record
from app.quality import METRIC_VERSION
from app.scorecards import Point, significant_drop

RESPONSE_WINDOW = timedelta(days=3)
RECENT_PERIODS = 8  # scorecard periods in the recent window of quality-volume-v1
MIN_GOLD = 60  # gold answers each side of quality-volume-v1 needs (as D19)
QUARANTINES = 2
QUARANTINE_DAYS = 30
MIN_DECIDED = 4  # decided worker cases before case-handling-v1 looks at the share cleared
MOSTLY_CLEARED = 0.75
REINSTATED = 2
OPEN_DAYS = 3
LEFT_OPEN = 2
RECONSIDER_AFTER = timedelta(days=7)  # a decided vendor case isn't reopened on the same rule sooner


async def open_or_join(session: AsyncSession, vendor_id: uuid.UUID, project_id: uuid.UUID | None, *, rule: str,
                       version: str, observation: str, evidence: dict) -> bool:
    """Record a vendor finding on the vendor's open case, opening one if needed. True if this
    finding is new on the case."""
    now = clock.now()
    case_id = await session.scalar(
        text("SELECT id FROM cases WHERE subject_type = 'vendor' AND subject_id = :v AND status = 'open'"), {"v": vendor_id})
    if case_id is None:
        case_id = await session.scalar(
            text("INSERT INTO cases (subject_type, subject_id, vendor_id, project_id, owner, opened_at) "
                 "VALUES ('vendor', :v, :v, :p, 'data_ops', :now) RETURNING id"),
            {"v": vendor_id, "p": project_id, "now": now},
        )
        await add_action(session, case_id, "opened", None, "Opened by the platform's vendor rules", now)
        # Anthropic's log until data ops shares the case with the vendor.
        record(session, actor=None, action="case.opened", entity_type="case", entity_id=case_id, org_id=None,
               after={"subject": "vendor", "vendor_id": str(vendor_id), "rule": rule})
    inserted = await session.scalar(
        text("INSERT INTO flags (case_id, rule, rule_version, observation, evidence, created_at) "
             "VALUES (:c, :r, :rv, :o, CAST(:e AS jsonb), :now) ON CONFLICT ON CONSTRAINT flags_once_per_rule_per_case DO NOTHING "
             "RETURNING id"),
        {"c": case_id, "r": rule, "rv": version, "o": observation, "e": json.dumps(evidence), "now": now},
    )
    return inserted is not None


async def quality_volume(session: AsyncSession) -> list[tuple]:
    rows = (await session.execute(
        text("""
            SELECT r.project_id, r.vendor_id, r.period_start, r.submissions, r.gold_n, r.gold_correct, r.compared, r.agreed,
                   r.rejected, r.reviewed, r.accepted, r.cost
            FROM scorecard_rows r JOIN projects p ON p.id = r.project_id
            WHERE r.metric_version = :m AND r.worker_id IS NULL AND p.status = 'active'
            ORDER BY r.project_id, r.vendor_id, r.period_start
        """), {"m": METRIC_VERSION})).all()
    series: dict[tuple, list[Point]] = {}
    for r in rows:
        series.setdefault((r.project_id, r.vendor_id), []).append(Point(
            period_start=r.period_start, submissions=r.submissions, gold_n=r.gold_n, gold_correct=r.gold_correct,
            compared=r.compared, agreed=r.agreed, rejected=r.rejected, reviewed=r.reviewed, accepted=r.accepted, cost=float(r.cost)))
    hits = []
    for (project_id, vendor_id), points in series.items():
        recent, before = points[-RECENT_PERIODS:], points[:-RECENT_PERIODS]
        r_n, b_n = sum(p.gold_n for p in recent), sum(p.gold_n for p in before)
        if not before or r_n < MIN_GOLD or b_n < MIN_GOLD:
            continue
        r_hits, b_hits = sum(p.gold_correct for p in recent), sum(p.gold_correct for p in before)
        r_volume = sum(p.submissions for p in recent) / len(recent)
        b_volume = sum(p.submissions for p in before) / len(before)
        if significant_drop(r_hits, r_n, b_hits, b_n, drop=0.08, z=2.0) and r_volume >= b_volume:
            hits.append((vendor_id, project_id, "quality-volume", "quality-volume-v1",
                         f"On one project, the vendor's accuracy on checked work fell from {b_hits / b_n:.0%} to "
                         f"{r_hits / r_n:.0%} while its volume rose from about {b_volume:.0f} to {r_volume:.0f} answers per period.",
                         {"project_id": str(project_id), "accuracy_before": round(b_hits / b_n, 3), "accuracy_recent": round(r_hits / r_n, 3),
                          "volume_before": round(b_volume, 1), "volume_recent": round(r_volume, 1)}))
    return hits


async def quarantines(session: AsyncSession) -> list[tuple]:
    since = clock.now() - timedelta(days=QUARANTINE_DAYS)
    rows = await session.execute(
        text("SELECT vendor_id, count(*) AS n, array_agg(subject_id) AS versions, "
             "CASE WHEN count(DISTINCT project_id) = 1 THEN min(project_id::text)::uuid END AS project_id FROM cases "
             "WHERE subject_type = 'environment_version' AND opened_at >= :since GROUP BY vendor_id HAVING count(*) >= :k"),
        {"since": since, "k": QUARANTINES})
    return [(r.vendor_id, r.project_id, "quarantines", "quarantines-v1",
             f"{r.n} of the vendor's environment versions were quarantined in the last {QUARANTINE_DAYS} days.",
             {"version_ids": [str(v) for v in r.versions]}) for r in rows]


async def case_handling(session: AsyncSession) -> list[tuple]:
    now = clock.now()
    rows = await session.execute(
        text("""
            SELECT c.vendor_id,
                   count(*) FILTER (WHERE c.status = 'decided') AS decided,
                   count(*) FILTER (WHERE c.decision = 'clear') AS cleared,
                   count(*) FILTER (WHERE c.status = 'open' AND c.opened_at < :stale) AS left_open,
                   count(*) FILTER (WHERE c.decision IN ('suspend', 'remove')
                                    AND EXISTS (SELECT 1 FROM case_actions a WHERE a.case_id = c.id AND a.action = 'reverse')) AS reinstated
            FROM cases c WHERE c.subject_type = 'worker' GROUP BY c.vendor_id
        """), {"stale": now - timedelta(days=OPEN_DAYS)})
    hits = []
    for r in rows:
        seen = []
        if r.decided >= MIN_DECIDED and r.cleared / r.decided >= MOSTLY_CLEARED:
            seen.append(f"{r.cleared} of its {r.decided} decided worker cases were cleared")
        if r.reinstated >= REINSTATED:
            seen.append(f"{r.reinstated} suspended or removed workers were reinstated")
        if r.left_open >= LEFT_OPEN:
            seen.append(f"{r.left_open} worker cases have been open for more than {OPEN_DAYS} days")
        if seen:
            hits.append((r.vendor_id, None, "case-handling", "case-handling-v1",
                         "How the vendor handles its own cases: " + "; ".join(seen) + ".",
                         {"decided": r.decided, "cleared": r.cleared, "reinstated": r.reinstated, "left_open": r.left_open}))
    return hits


RULES = [quality_volume, quarantines, case_handling]


async def run(session: AsyncSession) -> int:
    """The job: evaluate every vendor rule and record new findings. Returns how many were new."""
    new = 0
    for rule in RULES:
        for vendor_id, project_id, name, version, observation, evidence in await rule(session):
            if await session.scalar(text("SELECT offboarded_at IS NOT NULL FROM orgs WHERE id = :v"), {"v": vendor_id}):
                continue
            decided_lately = await session.scalar(
                text("SELECT 1 FROM cases c JOIN flags f ON f.case_id = c.id WHERE c.subject_type = 'vendor' AND c.subject_id = :v "
                     "AND c.status = 'decided' AND c.decided_at > :since AND f.rule = :r LIMIT 1"),
                {"v": vendor_id, "since": clock.now() - RECONSIDER_AFTER, "r": name})
            if decided_lately:
                continue  # a person weighed this recently (as D20 for workers)
            new += await open_or_join(session, vendor_id, project_id, rule=name, version=version,
                                      observation=observation, evidence=evidence)
    await session.commit()
    return new
