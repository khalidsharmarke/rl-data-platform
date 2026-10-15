"""What training sends back: episodes (TO1, D5, D6, D36, N4, N5).

Training signs in with its own token, as for the catalog. A batch is stored and acknowledged
with no processing inline: statistics, detection and dashboards all read the stored episodes
later, so nothing the platform does can make training wait (N4). Episodes are keyed by
training's own episode id; resending a batch, or part of one, changes nothing (D6).

Episodes are stored in sequence order under a transaction-scoped lock, for the same reason as
submission events (app/events.py): the hack detector reads them by sequence number and must
never skip one that commits late.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, environments
from app.db import get_session
from app.models import Environment, EnvironmentVersion
from app.routes.catalog import require_training

router = APIRouter()
EPISODE_ORDER_LOCK = 0x5E0E_0002
MAX_TRANSCRIPT_BYTES = 64 * 1024


class EpisodeIn(BaseModel):
    episode_id: str = Field(min_length=1, max_length=200)
    version_id: uuid.UUID
    step: int = Field(ge=0)
    reward: float
    passed: bool
    # How long the attempt took, in the training system's own unit (turns or tokens).
    length: int = Field(ge=0)
    # Raw facts for detectors, not conclusions (D35): the paths the attempt wrote to.
    touched: list[str] = []
    transcript: str | None = None


class BatchIn(BaseModel):
    run_label: str = Field(min_length=1, max_length=200)
    episodes: list[EpisodeIn] = Field(min_length=1, max_length=1000)


class BatchOut(BaseModel):
    run_label: str
    stored: int
    duplicates: int


@router.post("/ingest/episodes", dependencies=[Depends(require_training)])
async def ingest(body: BatchIn, session: AsyncSession = Depends(get_session)) -> BatchOut:
    version_ids = {e.version_id for e in body.episodes}
    vendors = dict((await session.execute(
        select(EnvironmentVersion.id, Environment.org_id)
        .join(Environment, Environment.id == EnvironmentVersion.environment_id)
        .where(EnvironmentVersion.id.in_(version_ids))
    )).all())
    unknown = version_ids - set(vendors)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown environment version {sorted(map(str, unknown))[0]}")
    for e in body.episodes:
        if e.transcript is not None and len(e.transcript.encode()) > MAX_TRANSCRIPT_BYTES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{e.episode_id}: transcript is larger than 64 KB")
    now = clock.now()
    await session.execute(text("INSERT INTO training_runs (label, first_seen_at) VALUES (:l, :now) ON CONFLICT (label) DO NOTHING"),
                          {"l": body.run_label, "now": now})
    run_id = await session.scalar(text("SELECT id FROM training_runs WHERE label = :l"), {"l": body.run_label})
    hashes = {e.episode_id: await environments.store(session, e.transcript.encode()) for e in body.episodes if e.transcript is not None}
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": EPISODE_ORDER_LOCK})
    stored = 0
    for e in body.episodes:
        result = await session.execute(
            text("INSERT INTO episodes (episode_id, run_id, step, version_id, vendor_id, reward, passed, length, touched, "
                 "transcript_hash, received_at) VALUES (:id, :run, :step, :v, :vendor, :reward, :passed, :length, :touched, :t, :now) "
                 "ON CONFLICT (episode_id) DO NOTHING"),
            {"id": e.episode_id, "run": run_id, "step": e.step, "v": e.version_id, "vendor": vendors[e.version_id],
             "reward": e.reward, "passed": e.passed, "length": e.length, "touched": e.touched,
             "t": hashes.get(e.episode_id), "now": now},
        )
        stored += result.rowcount
    await session.commit()
    return BatchOut(run_label=body.run_label, stored=stored, duplicates=len(body.episodes) - stored)
