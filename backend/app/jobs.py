"""The background worker: claims jobs from the Postgres queue (D2) and runs them.

Run as its own process: `python -m app.jobs`. Each tick it queues a scorecard roll-up for
every active project (a dedupe key keeps one pending per project), then works through the
queue. Jobs are claimed with FOR UPDATE SKIP LOCKED, so several workers can run safely.
Abuse detection isn't a job here: it runs in the separate detection service (D23).
"""

import asyncio
import json
import logging
import traceback
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.config import settings
from app.db import SessionLocal
from app.escalation import review as review_escalations
from app.scorecards import rollup_project
from app.training_stats import process as process_training_stats
from app.vendor_oversight import run as run_vendor_rules

log = logging.getLogger("jobs")

HANDLERS: dict[str, Callable[[AsyncSession, dict], Awaitable[object]]] = {
    "scorecards": lambda session, payload: rollup_project(session, uuid.UUID(payload["project_id"])),
    "escalations": lambda session, payload: review_escalations(session),
    "training_stats": lambda session, payload: process_training_stats(session),
    "vendor_rules": lambda session, payload: run_vendor_rules(session),
}


async def enqueue(session: AsyncSession, kind: str, dedupe_key: str, payload: dict) -> bool:
    result = await session.execute(
        text(
            "INSERT INTO jobs (kind, dedupe_key, payload) VALUES (:k, :d, CAST(:p AS jsonb)) "
            "ON CONFLICT (dedupe_key) WHERE status IN ('queued', 'running') DO NOTHING"
        ),
        {"k": kind, "d": dedupe_key, "p": json.dumps(payload)},
    )
    return result.rowcount == 1


async def schedule(session: AsyncSession) -> None:
    for (project_id,) in (await session.execute(text("SELECT id FROM projects WHERE status = 'active'"))).all():
        await enqueue(session, "scorecards", f"scorecards:{project_id}", {"project_id": str(project_id)})
    await enqueue(session, "escalations", "escalations", {})
    await enqueue(session, "training_stats", "training_stats", {})
    await enqueue(session, "vendor_rules", "vendor_rules", {})
    await session.commit()


async def run_one(session: AsyncSession) -> bool:
    row = (
        await session.execute(
            text(
                "UPDATE jobs SET status = 'running', attempts = attempts + 1 WHERE id = ("
                # Validation jobs need the sandbox runner (app/sandbox.py); this worker leaves them alone.
                "  SELECT id FROM jobs WHERE status = 'queued' AND kind <> 'validate_version' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
                ") RETURNING id, kind, payload"
            )
        )
    ).first()
    await session.commit()
    if row is None:
        return False
    try:
        await HANDLERS[row.kind](session, row.payload)
        await session.execute(text("UPDATE jobs SET status = 'done', finished_at = now() WHERE id = :id"), {"id": row.id})
    except Exception:
        await session.rollback()
        await session.execute(
            text("UPDATE jobs SET status = 'failed', finished_at = now(), error = :e WHERE id = :id"),
            {"id": row.id, "e": traceback.format_exc()[-2000:]},
        )
        log.exception("job %s failed", row.id)
    await session.commit()
    return True


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    async with SessionLocal() as session:
        await clock.start(session)
    log.info("worker started; clock speed %sx; tick every %ss", clock.speed(), settings.jobs_tick_seconds)
    while True:
        async with SessionLocal() as session:
            await schedule(session)
            while await run_one(session):
                pass
        await asyncio.sleep(settings.jobs_tick_seconds)


if __name__ == "__main__":
    asyncio.run(main())
