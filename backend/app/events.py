"""Submission events for the detection service (AB13, D23, D24).

Every submission writes one event in the same transaction, so no submission can commit
without its event (the outbox pattern). The detection service reads events in id order
through the API and reports how far it has got, so ids must be in commit order: a reader
that has seen event 10 must never later find a committed event 9. Identity values are
handed out at insert time, not commit time, so two transactions could commit out of order.
A transaction-scoped advisory lock taken just before the insert fixes that: it's held until
commit, so events are inserted and committed one transaction at a time. It serializes only
the last statement of each submission.

Events carry raw signals, not conclusions, so a detector can use them in ways nobody has
thought of yet without a contract change.
"""

import json
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.models import Submission, Task

EVENT_ORDER_LOCK = 0x5E0E_0001


def task_words(task: Task) -> int:
    payload = task.payload or {}
    words = " ".join(str(payload.get(k, "")) for k in ("prompt", "response", "response_a", "response_b"))
    return len(re.findall(r"\S+", words))


async def publish_submission(session: AsyncSession, submission: Submission, task: Task) -> None:
    """Write the submission's event. Call last in the transaction, right before commit."""
    payload = {
        "submission_id": str(submission.id),
        "worker_id": str(submission.worker_id),
        "vendor_id": str(submission.vendor_id),
        "project_id": str(submission.project_id),
        "task_id": str(submission.task_id),
        "submitted_at": submission.submitted_at.isoformat(),
        "time_on_task_ms": submission.time_on_task_ms,
        "task_words": task_words(task),
        "answer": submission.answer,
        # Reported by the browser: untrusted hints (AB11, A32).
        "client_signals": submission.client_signals or {},
    }
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": EVENT_ORDER_LOCK})
    await session.execute(
        text("INSERT INTO submission_events (submission_id, worker_id, project_id, payload, created_at) "
             "VALUES (:s, :w, :p, CAST(:payload AS jsonb), :now)"),
        {"s": submission.id, "w": submission.worker_id, "p": submission.project_id,
         "payload": json.dumps(payload), "now": clock.now()},
    )
