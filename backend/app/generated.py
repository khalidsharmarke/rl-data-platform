"""Generated tasks at scale (EC3, EC7, D49).

Researchers review a random sample of each generated batch: the square root of the versions that
passed the checks, at least three. Approving every sampled version approves the batch; sending
one back sends back the rest of the batch and stops its task family until a researcher resumes
it. Once training has used the tasks, each family's intended difficulty is set beside the
difficulty measured from training's solve rates.
"""

import math
import random
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, environments
from app.audit import record
from app.models import EnvironmentVersion, User
from app.training_stats import Stats

BATCH_VERSIONS = text("""
SELECT ev.id, ev.status, ev.manifest->'generated'->>'family' AS family, e.org_id
FROM environment_versions ev JOIN environments e ON e.id = ev.environment_id
WHERE e.project_id = :p AND ev.manifest->'generated'->>'batch' = :b
""")


def sample_size(passed: int) -> int:
    return min(passed, max(3, math.ceil(math.sqrt(passed))))


async def ready(session: AsyncSession, project_id: uuid.UUID, batch: str, org_id: uuid.UUID, actor) -> dict:
    """The generator says a batch is complete: draw the sample researchers review (D49)."""
    rows = [r for r in (await session.execute(BATCH_VERSIONS, {"p": project_id, "b": batch})).all() if r.org_id == org_id]
    passed = [r.id for r in rows if r.status == "in_review"]
    sample = random.SystemRandom().sample(passed, sample_size(len(passed)))
    status = "in_review" if sample else "nothing_passed"
    await session.execute(text("INSERT INTO generated_batches (project_id, batch, org_id, status, ready_at) VALUES (:p, :b, :o, :s, :now)"),
                          {"p": project_id, "b": batch, "o": org_id, "s": status, "now": clock.now()})
    for version_id in sample:
        await session.execute(text("INSERT INTO batch_samples (version_id, project_id, batch) VALUES (:v, :p, :b)"),
                              {"v": version_id, "p": project_id, "b": batch})
    record(session, actor=actor, action="generated_batch.ready", entity_type="generated_batch", entity_id=f"{project_id}/{batch}",
           org_id=org_id, after={"versions": len(rows), "passed_checks": len(passed), "sampled": len(sample)})
    return {"batch": batch, "versions": len(rows), "passed_checks": len(passed), "sampled": len(sample), "status": status}


async def after_review(session: AsyncSession, version: EnvironmentVersion, decision: str, user: User) -> None:
    """A sampled version was reviewed: settle its batch when the sample says so."""
    await session.flush()  # the review's own transition must count below
    sampled = (await session.execute(text("SELECT project_id, batch FROM batch_samples WHERE version_id = :v"), {"v": version.id})).first()
    if sampled is None:
        return
    p, b = sampled
    rows = (await session.execute(BATCH_VERSIONS, {"p": p, "b": b})).all()
    family, org_id = version.manifest["generated"]["family"], rows[0].org_id
    if decision == "send_back":
        await settle(session, rows, "failed", user, f"Sent back with its batch: a sampled version of {family} was sent back")
        await session.execute(text("INSERT INTO family_stops (org_id, family, reason, stopped_by, stopped_at) VALUES (:o, :f, :r, :u, :now) "
                                   "ON CONFLICT (org_id, family) WHERE resumed_at IS NULL DO NOTHING"),
                              {"o": org_id, "f": family, "r": f"A sampled version from {b} was sent back", "u": user.id, "now": clock.now()})
        await decide_batch(session, p, b, "sent_back", user, org_id, family_stopped=family)
        return
    pending = await session.scalar(text("SELECT count(*) FROM batch_samples s JOIN environment_versions ev ON ev.id = s.version_id "
                                        "WHERE s.project_id = :p AND s.batch = :b AND ev.status = 'in_review'"), {"p": p, "b": b})
    if pending == 0:
        await settle(session, rows, "approved", user, "Approved with its batch: every sampled version was approved (D49)")
        await decide_batch(session, p, b, "approved", user, org_id)


async def settle(session: AsyncSession, rows, to: str, user: User, note: str) -> None:
    for r in rows:
        if r.status == "in_review":
            version = await session.get(EnvironmentVersion, r.id)
            if version.status == "in_review":
                await environments.transition(session, version, to, actor=user, note=note)


async def decide_batch(session, project_id, batch, status, user, org_id, family_stopped: str | None = None) -> None:
    await session.execute(text("UPDATE generated_batches SET status = :s, decided_at = :now WHERE project_id = :p AND batch = :b"),
                          {"s": status, "now": clock.now(), "p": project_id, "b": batch})
    record(session, actor=user, action=f"generated_batch.{status}", entity_type="generated_batch", entity_id=f"{project_id}/{batch}",
           org_id=org_id, after={"family_stopped": family_stopped} if family_stopped else None)


SUMMARY = text("""
SELECT ev.manifest->'generated'->>'family' AS family, ev.manifest->'generated'->>'difficulty' AS difficulty,
       count(DISTINCT ev.id) AS versions, count(DISTINCT ev.id) FILTER (WHERE ev.status = 'approved') AS approved,
       count(DISTINCT ev.id) FILTER (WHERE ev.status = 'failed') AS failed,
       coalesce(sum(t.episodes), 0) AS episodes, coalesce(sum(t.passed), 0) AS passed,
       coalesce(sum(t.reward_sum), 0) AS reward_sum, coalesce(sum(t.reward_sq_sum), 0) AS reward_sq_sum,
       coalesce(sum(t.length_sum), 0) AS length_sum
FROM environment_versions ev JOIN environments e ON e.id = ev.environment_id
LEFT JOIN training_stats t ON t.version_id = ev.id
WHERE e.project_id = :p AND ev.manifest ? 'generated'
GROUP BY 1, 2 ORDER BY 1, array_position(ARRAY['easy', 'medium', 'hard'], ev.manifest->'generated'->>'difficulty')
""")

BATCHES = text("""
SELECT b.batch, b.status, b.ready_at, b.decided_at,
       (SELECT count(*) FROM environment_versions ev JOIN environments e ON e.id = ev.environment_id
         WHERE e.project_id = b.project_id AND ev.manifest->'generated'->>'batch' = b.batch) AS versions,
       (SELECT count(*) FROM batch_samples s WHERE s.project_id = b.project_id AND s.batch = b.batch) AS sampled,
       (SELECT count(*) FROM batch_samples s JOIN environment_versions ev ON ev.id = s.version_id
         WHERE s.project_id = b.project_id AND s.batch = b.batch AND ev.status <> 'in_review') AS sample_reviewed
FROM generated_batches b WHERE b.project_id = :p ORDER BY b.ready_at DESC
""")


async def summary(session: AsyncSession, project_id: uuid.UUID) -> dict:
    families = []
    for r in await session.execute(SUMMARY, {"p": project_id}):
        stats = Stats(r.episodes, r.passed, r.reward_sum, r.reward_sq_sum, r.length_sum)
        families.append({"family": r.family, "intended": r.difficulty, "versions": r.versions, "approved": r.approved, "failed": r.failed,
                         "episodes": r.episodes, "solve_rate": stats.solve_rate, "measured": stats.difficulty})
    stops = (await session.execute(text(
        "SELECT id, family, reason, stopped_at FROM family_stops WHERE resumed_at IS NULL "
        "AND org_id IN (SELECT org_id FROM environments WHERE project_id = :p) ORDER BY stopped_at"), {"p": project_id})).all()
    return {"families": families, "batches": [dict(r._mapping) for r in await session.execute(BATCHES, {"p": project_id})],
            "stopped": [dict(r._mapping) for r in stops]}
