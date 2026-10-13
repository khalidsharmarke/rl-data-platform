"""Running the abuse rules and opening cases (AB1, AB3, AB8, D13, D16).

A finding opens a case for the worker's vendor admin (or joins the worker's open case), and
the platform immediately holds the worker's answers: reversible, protective, automatic.
Anything done to the worker as a person waits for a human decision.
"""

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.audit import record
from app.rules import WINDOW, Answer, evaluate

RECENT = text(
    """
    SELECT s.id, s.worker_id, s.vendor_id, s.time_on_task_ms, s.answer->>'rationale' AS rationale,
           coalesce((s.client_signals->>'pasted')::boolean, false) AS pasted,
           array_length(regexp_split_to_array(trim(
               coalesce(t.payload->>'prompt', '') || ' ' || coalesce(t.payload->>'response_a', '') || ' ' ||
               coalesce(t.payload->>'response_b', '')), '\\s+'), 1) AS words,
           row_number() OVER (PARTITION BY s.worker_id ORDER BY s.submitted_at DESC) AS n
    FROM submissions s
    JOIN tasks t ON t.id = s.task_id
    JOIN users u ON u.id = s.worker_id
    WHERE s.project_id = :p
      AND u.suspended_at IS NULL
      -- after a decision, judge only what happens next
      AND s.submitted_at > coalesce((
            SELECT max(c.decided_at) FROM cases c WHERE c.subject_type = 'worker' AND c.subject_id = s.worker_id
          ), '-infinity')
    """
)


async def scan_project(session: AsyncSession, project_id: uuid.UUID) -> int:
    rows = (await session.execute(text(f"SELECT * FROM ({RECENT.text}) x WHERE n <= :w"), {"p": project_id, "w": WINDOW})).all()
    by_worker: dict[uuid.UUID, list] = {}
    for r in rows:
        by_worker.setdefault(r.worker_id, []).append(r)
    opened = 0
    for worker_id, recent in by_worker.items():
        window = [Answer(str(r.id), r.time_on_task_ms, r.words or 0, r.pasted, r.rationale) for r in recent]
        findings = evaluate(window)
        if findings:
            opened += await open_or_join_case(session, worker_id=worker_id, vendor_id=recent[0].vendor_id, project_id=project_id, findings=findings)
    await session.commit()
    return opened


async def open_or_join_case(session, *, worker_id, vendor_id, project_id, findings) -> int:
    now = clock.now()
    case_id = await session.scalar(
        text("SELECT id FROM cases WHERE subject_type = 'worker' AND subject_id = :w AND status = 'open'"), {"w": worker_id}
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
    return opened


async def add_action(session, case_id, action, actor_id, note, at) -> None:
    await session.execute(
        text("INSERT INTO case_actions (case_id, action, actor_id, note, created_at) VALUES (:c, :a, :u, :n, :t)"),
        {"c": case_id, "a": action, "u": actor_id, "n": note, "t": at},
    )
