"""Flagging an environment version: the case, the quarantine and the recall (AB3, AB8, TO6,
TO8, D13, D16, D37).

One path for every flag, whether a person raised it or the hack detector did (D7). The first
flag on an approved version opens a case owned by data ops and quarantines the version at once:
it leaves the catalog, so no new run can pull it. If training has already received the version,
the platform issues a recall at the same moment: each run that pulled it, the first step it
appeared at, how many episodes it had, and where flagged attempts began. Training decides what
to do with each run and reports back (PD8); the platform never touches a model.
"""

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, environments
from app.abuse import add_action
from app.audit import record
from app.models import Environment, EnvironmentVersion, User


async def flag_version(session: AsyncSession, version: EnvironmentVersion, *, observation: str, actor: User | None,
                       rule: str = "person", rule_version: str = "person", evidence: dict | None = None) -> uuid.UUID | None:
    """Open (or join) the version's case. Returns the case id, or None if the version can't be flagged."""
    env = await session.get(Environment, version.environment_id)
    now = clock.now()
    case_id = await session.scalar(
        text("SELECT id FROM cases WHERE subject_type = 'environment_version' AND subject_id = :v AND status = 'open'"), {"v": version.id}
    )
    if case_id is None:
        if version.status != "approved":
            return None
        case_id = await session.scalar(
            text("INSERT INTO cases (subject_type, subject_id, vendor_id, project_id, owner, opened_at) "
                 "VALUES ('environment_version', :v, :o, :p, 'data_ops', :now) RETURNING id"),
            {"v": version.id, "o": env.org_id, "p": env.project_id, "now": now},
        )
        await add_action(session, case_id, "opened", actor.id if actor else None, observation, now)
        await environments.transition(session, version, "quarantined", actor=actor, note=observation)
        await add_action(session, case_id, "hold", None, "Version quarantined: out of the catalog while the case is open", now)
        record(session, actor=actor, action="case.opened", entity_type="case", entity_id=case_id, org_id=env.org_id,
               after={"subject": "environment_version", "version_id": str(version.id), "quarantined": True, "by": rule})
        new_case = True
    else:
        new_case = False
    await session.execute(
        text("INSERT INTO flags (case_id, rule, rule_version, observation, evidence, created_at) "
             "VALUES (:c, :r, :rv, :o, CAST(:e AS jsonb), :now) ON CONFLICT ON CONSTRAINT flags_once_per_rule_per_case DO NOTHING"),
        {"c": case_id, "r": rule, "rv": rule_version, "o": observation, "e": json.dumps(evidence or {}), "now": now},
    )
    if not new_case:
        await add_action(session, case_id, "flag", actor.id if actor else None, observation, now)
    else:
        await issue_recall(session, case_id, version.id)
    return case_id


async def issue_recall(session: AsyncSession, case_id: uuid.UUID, version_id: uuid.UUID) -> int | None:
    """Tell training which runs received this version and from when. None if no run ever pulled it."""
    runs = (await session.execute(text(
        """
        WITH pulled AS (SELECT DISTINCT run_label FROM catalog_pulls WHERE version_id = :v AND run_label IS NOT NULL),
        used AS (
            SELECT r.label, min(e.step) AS first_step, max(e.step) AS last_step, count(*) AS episodes
            FROM episodes e JOIN training_runs r ON r.id = e.run_id WHERE e.version_id = :v GROUP BY r.label
        ),
        flagged AS (
            -- each finding carries a running total of flagged attempts, so take the largest
            SELECT r.label, max((f.evidence->>'flagged_episodes')::int) AS flagged, min((f.evidence->>'first_flagged_step')::int) AS first_flagged_step
            FROM hack_findings f JOIN training_runs r ON r.id = f.run_id WHERE f.version_id = :v GROUP BY r.label
        )
        SELECT coalesce(u.label, p.run_label) AS run_label, u.first_step, u.last_step, coalesce(u.episodes, 0) AS episodes,
               fl.flagged, fl.first_flagged_step
        FROM pulled p FULL JOIN used u ON u.label = p.run_label LEFT JOIN flagged fl ON fl.label = coalesce(u.label, p.run_label)
        ORDER BY 1
        """), {"v": version_id})).all()
    if not runs:
        return None
    payload = [dict(r._mapping) for r in runs]
    recall_id = await session.scalar(
        text("INSERT INTO recalls (case_id, version_id, runs, issued_at) VALUES (:c, :v, CAST(:r AS jsonb), :now) RETURNING id"),
        {"c": case_id, "v": version_id, "r": json.dumps(payload), "now": clock.now()},
    )
    summary = "; ".join(f"{r['run_label']} from step {r['first_step']:,}" if r["first_step"] is not None else f"{r['run_label']} (pulled, unused)"
                        for r in payload)
    await add_action(session, case_id, "recall", None, f"Recall issued to training: {summary}", clock.now())
    return recall_id
