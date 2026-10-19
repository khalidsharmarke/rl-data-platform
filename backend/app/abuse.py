"""Opening cases from findings (AB3, AB8, D13, D16).

Detection happens in the separate detection service (AB13, D23); the platform owns what a
finding leads to. A finding at case severity opens a case for the worker's vendor admin (or
joins the worker's open case), and the platform immediately holds the worker's answers:
reversible, protective, automatic. Anything done to the worker as a person waits for a
human decision.
"""

import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text

from app import clock
from app.audit import record


@dataclass(frozen=True)
class Observation:
    """One signal behind a case, as the vendor admin will read it (N17: no thresholds)."""

    rule: str
    version: str
    observation: str
    evidence: dict


async def open_or_join_case(session, *, worker_id, vendor_id, project_id, findings: list[Observation]) -> tuple[uuid.UUID, int]:
    now = clock.now()
    case_id = await session.scalar(
        text("SELECT id FROM cases WHERE subject_type = 'worker' AND subject_id = :w AND status = 'open' AND NOT private"), {"w": worker_id}
    )
    opened = 0
    if case_id is None:
        case_id = await session.scalar(
            text(
                "INSERT INTO cases (subject_type, subject_id, vendor_id, project_id, owner, opened_at) "
                "VALUES ('worker', :w, :v, :p, 'vendor_admin', :now) RETURNING id"
            ),
            {"w": worker_id, "v": vendor_id, "p": project_id, "now": now},
        )
        await add_action(session, case_id, "opened", None, "Opened by the platform's abuse signals", now)
        await session.execute(
            text("INSERT INTO holds (case_id, worker_id, created_at) VALUES (:c, :w, :now)"), {"c": case_id, "w": worker_id, "now": now}
        )
        await add_action(session, case_id, "hold", None, "Worker's answers held out of exports while the case is open", now)
        record(session, actor=None, action="case.opened", entity_type="case", entity_id=case_id, org_id=vendor_id,
               after={"subject": "worker", "worker_id": str(worker_id), "hold": True})
        opened = 1
    for f in findings:
        await session.execute(
            text(
                "INSERT INTO flags (case_id, rule, rule_version, observation, evidence, created_at) "
                "VALUES (:c, :r, :v, :o, CAST(:e AS jsonb), :now) ON CONFLICT ON CONSTRAINT flags_once_per_rule_per_case DO NOTHING"
            ),
            {"c": case_id, "r": f.rule, "v": f.version, "o": f.observation, "e": json.dumps(f.evidence), "now": now},
        )
    return case_id, opened


async def add_action(session, case_id, action, actor_id, note, at) -> None:
    await session.execute(
        text("INSERT INTO case_actions (case_id, action, actor_id, note, created_at) VALUES (:c, :a, :u, :n, :t)"),
        {"c": case_id, "a": action, "u": actor_id, "n": note, "t": at},
    )
