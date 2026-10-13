"""Routing tasks to vendors and handing tasks to workers.

Routing: each regular task goes to one vendor, in proportion to the active allocations'
shares. Gold tasks go to every allocated vendor, so all vendors are measured on the same
tests. Re-routing only moves tasks nobody has started.

Claiming (D2): a worker takes the next task routed to their vendor with
SELECT ... FOR UPDATE SKIP LOCKED on the route row, so concurrent workers at one vendor
never wait on each other. The overlap check in that query can be stale: its snapshot is
taken before the lock is granted, and a claim committed in between isn't visible to it.
So after locking, the claim recounts in a fresh statement (READ COMMITTED gives each
statement a new snapshot). Every claim on that route holds the same lock, so the recount
is exact; if the task is already full, the claim moves on to the next one.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.models import Allocation, Assignment, GoldAnswer, Project, Task, TaskRoute

LEASE = timedelta(minutes=20)


def split_by_share(count: int, shares: dict[uuid.UUID, Decimal]) -> dict[uuid.UUID, int]:
    """Largest-remainder apportionment of `count` items across shares (normalized)."""
    total = sum(shares.values())
    if count == 0 or total == 0:
        return {k: 0 for k in shares}
    exact = {k: Decimal(count) * v / total for k, v in shares.items()}
    result = {k: int(x) for k, x in exact.items()}
    leftover = count - sum(result.values())
    for k in sorted(exact, key=lambda k: (exact[k] - result[k], str(k)), reverse=True)[:leftover]:
        result[k] += 1
    return result


def interleave(counts: dict[uuid.UUID, int]) -> list[uuid.UUID]:
    """Spread each vendor's tasks evenly through the claim order (smooth weighted round-robin).

    Gold tasks go to every vendor and sit evenly through the whole order. If each vendor's
    regular tasks were one contiguous block instead, a vendor's stream would end in a run of
    gold tasks, which would make them recognizable (D8).
    """
    total = sum(counts.values())
    given = dict.fromkeys(counts, 0)
    order = []
    for i in range(total):
        vendor = max(
            (v for v in counts if given[v] < counts[v]),
            key=lambda v: (counts[v] * (i + 1) / total - given[v], str(v)),
        )
        given[vendor] += 1
        order.append(vendor)
    return order


async def route_tasks(session: AsyncSession, project: Project) -> dict[str, int]:
    allocations = (
        await session.scalars(select(Allocation).where(Allocation.project_id == project.id, Allocation.status == "active"))
    ).all()
    vendors = {a.vendor_id: a.share for a in allocations}
    gold_ids = select(GoldAnswer.task_id)
    started = select(Assignment.task_id).where(Assignment.project_id == project.id)

    # Regular tasks nobody has started are free to move.
    await session.execute(
        delete(TaskRoute).where(
            TaskRoute.task_id.in_(select(Task.id).where(Task.project_id == project.id)),
            TaskRoute.task_id.not_in(gold_ids),
            TaskRoute.task_id.not_in(started),
        )
    )
    unrouted = (
        await session.scalars(
            select(Task.id)
            .where(Task.project_id == project.id, Task.id.not_in(gold_ids), Task.id.not_in(select(TaskRoute.task_id)))
            .order_by(Task.seq)
        )
    ).all()
    counts = split_by_share(len(unrouted), vendors)
    for task_id, vendor_id in zip(unrouted, interleave(counts)):
        session.add(TaskRoute(task_id=task_id, vendor_id=vendor_id, routed_at=clock.now()))

    # Every allocated vendor gets every gold task.
    gold_tasks = (await session.scalars(select(Task.id).where(Task.project_id == project.id, Task.id.in_(gold_ids)))).all()
    existing = set((await session.execute(select(TaskRoute.task_id, TaskRoute.vendor_id).where(TaskRoute.task_id.in_(gold_tasks)))).all())
    for task_id in gold_tasks:
        for vendor_id in vendors:
            if (task_id, vendor_id) not in existing:
                session.add(TaskRoute(task_id=task_id, vendor_id=vendor_id, routed_at=clock.now()))
    await session.flush()
    return {"regular_routed": len(unrouted), "gold_tasks": len(gold_tasks), "vendors": len(vendors)}


CLAIM = text(
    """
    SELECT r.task_id, a.id AS allocation_id
    FROM task_routes r
    JOIN tasks t ON t.id = r.task_id
    JOIN projects p ON p.id = t.project_id
    JOIN allocations a ON a.project_id = p.id AND a.vendor_id = r.vendor_id
    WHERE r.vendor_id = :vendor_id
      AND NOT r.is_full
      AND p.status = 'active'
      AND a.status = 'active'
      AND (CAST(:project_id AS uuid) IS NULL OR p.id = CAST(:project_id AS uuid))
      AND r.task_id <> ALL(CAST(:skip AS uuid[]))
      AND NOT EXISTS (SELECT 1 FROM gold_retirements g WHERE g.task_id = r.task_id)
      AND NOT EXISTS (SELECT 1 FROM assignments x WHERE x.task_id = r.task_id AND x.worker_id = :worker_id)
      AND (
        SELECT count(*) FROM assignments x
        WHERE x.task_id = r.task_id AND x.vendor_id = r.vendor_id
          AND (x.status = 'submitted' OR (x.status = 'open' AND x.expires_at > :now))
      ) < p.overlap
    ORDER BY t.seq
    LIMIT 1
    FOR UPDATE OF r SKIP LOCKED
    """
)


RECOUNT = text(
    """
    SELECT count(*) FROM assignments x
    WHERE x.task_id = :task_id AND x.vendor_id = :vendor_id
      AND (x.status = 'submitted' OR (x.status = 'open' AND x.expires_at > :now))
    """
)

MAX_ATTEMPTS = 20


async def claim_next(
    session: AsyncSession, *, worker_id: uuid.UUID, vendor_id: uuid.UUID, project_id: uuid.UUID | None
) -> Assignment | None:
    await expire_stale(session)
    skip: list[uuid.UUID] = []
    for _ in range(MAX_ATTEMPTS):
        params = {"vendor_id": vendor_id, "worker_id": worker_id, "project_id": project_id, "skip": skip, "now": clock.now()}
        row = (await session.execute(CLAIM, params)).first()
        if row is None:
            return None
        project = await session.get(Project, (await session.get(Task, row.task_id)).project_id)
        live = await session.scalar(RECOUNT, {"task_id": row.task_id, "vendor_id": vendor_id, "now": clock.now()})
        if live < project.overlap:
            break
        await mark_full(session, row.task_id, vendor_id, True)
        skip.append(row.task_id)
    else:
        return None
    task = await session.get(Task, row.task_id)
    now = clock.now()
    assignment = Assignment(
        task_id=task.id,
        project_id=task.project_id,
        vendor_id=vendor_id,
        worker_id=worker_id,
        allocation_id=row.allocation_id,
        assigned_at=now,
        expires_at=now + LEASE,
    )
    session.add(assignment)
    await session.flush()
    if live + 1 >= project.overlap:
        await mark_full(session, task.id, vendor_id, True)
    return assignment


async def mark_full(session: AsyncSession, task_id: uuid.UUID, vendor_id: uuid.UUID, full: bool) -> None:
    await session.execute(
        text("UPDATE task_routes SET is_full = :full WHERE task_id = :t AND vendor_id = :v"),
        {"full": full, "t": task_id, "v": vendor_id},
    )


async def expire_stale(session: AsyncSession) -> int:
    """Expire lapsed leases and reopen their routes, so the tasks can be claimed again."""
    expired = (
        await session.execute(
            text(
                "UPDATE assignments SET status = 'expired' WHERE status = 'open' AND expires_at <= :now "
                "RETURNING task_id, vendor_id"
            ),
            {"now": clock.now()},
        )
    ).all()
    for task_id, vendor_id in set(expired):
        await mark_full(session, task_id, vendor_id, False)
    return len(expired)


async def counts_by_vendor(session: AsyncSession, project_id: uuid.UUID) -> dict[uuid.UUID, dict[str, int]]:
    routed = await session.execute(
        select(TaskRoute.vendor_id, func.count())
        .join(Task, Task.id == TaskRoute.task_id)
        .where(Task.project_id == project_id)
        .group_by(TaskRoute.vendor_id)
    )
    submitted = await session.execute(
        text("SELECT vendor_id, count(*) FROM submissions WHERE project_id = :p GROUP BY vendor_id"), {"p": project_id}
    )
    out: dict[uuid.UUID, dict[str, int]] = {}
    for vendor_id, n in routed:
        out.setdefault(vendor_id, {"routed_tasks": 0, "submissions": 0})["routed_tasks"] = n
    for vendor_id, n in submitted:
        out.setdefault(vendor_id, {"routed_tasks": 0, "submissions": 0})["submissions"] = n
    return out
