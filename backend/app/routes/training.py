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
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, environments
from app.config import settings
from app.db import get_session
from app.models import Environment, EnvironmentVersion, Role, User
from app.routes.catalog import require_training
from app.scoping import require_roles
from app.training_stats import Stats

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


# Dashboards (TO2, TO4, TO5, D39): Anthropic only, from the platform's own statistics.

class RunRow(BaseModel):
    id: uuid.UUID
    label: str
    first_seen_at: datetime
    episodes: int
    environments: int
    last_step: int | None
    last_episode_at: datetime | None


class StepPoint(BaseModel):
    step: int
    episodes: int
    solve_rate: float | None
    mean_length: float | None


class VersionStats(BaseModel):
    version_id: uuid.UUID
    environment_id: uuid.UUID
    environment_name: str
    vendor_name: str
    number: int
    status: str
    episodes: int
    solve_rate: float | None
    mean_reward: float | None
    reward_variance: float | None
    mean_length: float | None
    difficulty: str | None
    series: list[StepPoint]


class RunDetail(BaseModel):
    run: RunRow
    processed_at: datetime | None
    unprocessed: int
    versions: list[VersionStats]


class EpisodeRow(BaseModel):
    episode_id: str
    step: int
    reward: float
    passed: bool
    length: int
    touched: list[str]
    received_at: datetime


class EpisodeDetail(EpisodeRow):
    run_label: str
    version_id: uuid.UUID
    environment_name: str
    vendor_name: str
    transcript: str | None


RUNS = """
SELECT r.id, r.label, r.first_seen_at, count(e.episode_id) AS episodes, count(DISTINCT e.version_id) AS environments,
       max(e.step) AS last_step, max(e.received_at) AS last_episode_at
FROM training_runs r LEFT JOIN episodes e ON e.run_id = r.id
{where}
GROUP BY r.id ORDER BY r.first_seen_at DESC
"""


@router.get("/runs")
async def list_runs(user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)), session: AsyncSession = Depends(get_session)) -> list[RunRow]:
    return [RunRow(**r._mapping) for r in await session.execute(text(RUNS.format(where="")))]


@router.get("/runs/{run_id}")
async def run_detail(run_id: uuid.UUID, user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)),
                     session: AsyncSession = Depends(get_session)) -> RunDetail:
    """One run: each environment's statistics overall and over the run's steps, from the processed episodes."""
    row = (await session.execute(text(RUNS.format(where="WHERE r.id = :r")), {"r": run_id})).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    progress = (await session.execute(text("SELECT through_seq, updated_at FROM training_stats_progress WHERE id = 1"))).one()
    unprocessed = await session.scalar(text("SELECT count(*) FROM episodes WHERE run_id = :r AND seq > :t"),
                                       {"r": run_id, "t": progress.through_seq})
    stats = await session.execute(text(
        """
        SELECT s.*, e.id AS environment_id, e.name AS environment_name, o.name AS vendor_name, v.number, v.status
        FROM training_stats s
        JOIN environment_versions v ON v.id = s.version_id
        JOIN environments e ON e.id = v.environment_id
        JOIN orgs o ON o.id = e.org_id
        WHERE s.run_id = :r ORDER BY e.name, s.step_bucket
        """), {"r": run_id})
    grouped: dict[uuid.UUID, list] = {}
    for s in stats:
        grouped.setdefault(s.version_id, []).append(s)
    versions = []
    for version_id, buckets in grouped.items():
        first = buckets[0]
        total = Stats(*(sum(getattr(b, k) for b in buckets) for k in ("episodes", "passed", "reward_sum", "reward_sq_sum", "length_sum")))
        series = [StepPoint(step=b.step_bucket * settings.training_step_bucket, episodes=b.episodes,
                            solve_rate=Stats(b.episodes, b.passed, b.reward_sum, b.reward_sq_sum, b.length_sum).solve_rate,
                            mean_length=Stats(b.episodes, b.passed, b.reward_sum, b.reward_sq_sum, b.length_sum).mean_length)
                  for b in buckets]
        versions.append(VersionStats(version_id=version_id, environment_id=first.environment_id, environment_name=first.environment_name,
                                     vendor_name=first.vendor_name, number=first.number, status=first.status, series=series, **total.as_dict()))
    return RunDetail(run=RunRow(**row._mapping), processed_at=progress.updated_at, unprocessed=unprocessed, versions=versions)


@router.get("/runs/{run_id}/episodes")
async def run_episodes(run_id: uuid.UUID, version_id: uuid.UUID, limit: int = Query(20, ge=1, le=200),
                       user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)),
                       session: AsyncSession = Depends(get_session)) -> list[EpisodeRow]:
    """The latest episodes of one environment in one run, newest first (TO5)."""
    rows = await session.execute(text(
        "SELECT episode_id, step, reward, passed, length, touched, received_at FROM episodes "
        "WHERE run_id = :r AND version_id = :v ORDER BY seq DESC LIMIT :n"), {"r": run_id, "v": version_id, "n": limit})
    return [EpisodeRow(**r._mapping) for r in rows]


@router.get("/episodes/{episode_id}")
async def episode_detail(episode_id: str, user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)),
                         session: AsyncSession = Depends(get_session)) -> EpisodeDetail:
    """One episode with its transcript (TO5)."""
    r = (await session.execute(text(
        "SELECT e.*, tr.label AS run_label, env.name AS environment_name, o.name AS vendor_name FROM episodes e "
        "JOIN training_runs tr ON tr.id = e.run_id JOIN environment_versions v ON v.id = e.version_id "
        "JOIN environments env ON env.id = v.environment_id JOIN orgs o ON o.id = e.vendor_id WHERE e.episode_id = :id"),
        {"id": episode_id})).first()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    transcript = environments.read(r.transcript_hash).decode(errors="replace") if r.transcript_hash else None
    return EpisodeDetail(episode_id=r.episode_id, step=r.step, reward=r.reward, passed=r.passed, length=r.length, touched=r.touched,
                         received_at=r.received_at, run_label=r.run_label, version_id=r.version_id,
                         environment_name=r.environment_name, vendor_name=r.vendor_name, transcript=transcript)
