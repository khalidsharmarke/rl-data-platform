"""M5 done-check pieces: roll-ups with metric versions and costs, the vendor comparison,
the vendor's own view with gold held back (PD2), and drop alerts (VP2-VP4, D11, N16)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app import clock
from app.config import settings
from app.jobs import enqueue, run_one, schedule
from app.scorecards import Point, drop_alerts, rollup_project
from tests.builders import allocate, answer_all, make_contract, make_project
from tests.people import (BRIGHTLINE_ADMIN, DATA_OPS, KESTREL_ADMIN, KESTREL_WORKER, NORTHWIND_ADMIN, NORTHWIND_WORKER,
                          NORTHWIND_WORKER_2)


async def two_vendor_project(client, login, session):
    """Northwind on a per-task contract, Kestrel hourly; one worker each answers everything."""
    project_id, generated = await make_project(client, login, tasks=20, gold_every=4)
    await allocate(client, login, project_id, "Northwind Data", "0.5", await make_contract(client, login, "Northwind Data", price="1.00", unit="task"))
    await allocate(client, login, project_id, "Kestrel Labs", "0.5", await make_contract(client, login, "Kestrel Labs", price="36.00", unit="hour"))
    await answer_all(client, await login(NORTHWIND_WORKER), project_id, generated, correct=True)
    await answer_all(client, await login(KESTREL_WORKER), project_id, generated, correct=False)
    return project_id


async def test_rollup_records_metric_version_and_contract_costs(client, login, session):
    project_id = await two_vendor_project(client, login, session)
    # Make time on task known: every Kestrel answer took exactly 100 seconds.
    await session.execute(text("ALTER TABLE submissions DISABLE TRIGGER submissions_immutable"))
    await session.execute(text("UPDATE submissions SET time_on_task_ms = 100000 FROM orgs o WHERE o.id = vendor_id AND o.name = 'Kestrel Labs'"))
    await session.execute(text("ALTER TABLE submissions ENABLE TRIGGER submissions_immutable"))
    await rollup_project(session, project_id)

    versions = await session.scalar(text("SELECT array_agg(DISTINCT metric_version) FROM scorecard_rows WHERE project_id = :p"), {"p": project_id})
    assert versions == ["quality-v1"]
    vendors = {v["vendor_name"]: v for v in (await client.get(f"/projects/{project_id}/vendors", headers=await login(DATA_OPS))).json()["vendors"]}
    northwind, kestrel = vendors["Northwind Data"], vendors["Kestrel Labs"]
    # Each vendor answered its share of the 15 regular tasks plus all 5 gold tasks.
    assert northwind["submissions"] + kestrel["submissions"] == 15 + 2 * 5
    # Per task at $1: every submitted answer is paid; only regular answers can be accepted.
    assert northwind["cost"] == northwind["submissions"] * 1.0
    assert northwind["accepted"] == northwind["submissions"] - 5
    assert northwind["cost_per_accepted"] == northwind["cost"] / northwind["accepted"]
    # Hourly at $36: 100 s per answer is $1 per answer; nothing accepted (0% gold accuracy).
    assert abs(kestrel["cost"] - kestrel["submissions"] * 1.0) < 0.01
    assert kestrel["accepted"] == 0 and kestrel["cost_per_accepted"] is None
    assert northwind["gold_accuracy"] == 1.0 and kestrel["gold_accuracy"] == 0.0


async def test_vendor_admin_sees_only_their_own_scorecard(client, login, session):
    project_id = await two_vendor_project(client, login, session)
    await rollup_project(session, project_id)
    vendors = {v["vendor_name"]: v["vendor_id"] for v in (await client.get(f"/projects/{project_id}/vendors", headers=await login(DATA_OPS))).json()["vendors"]}
    northwind_admin = await login(NORTHWIND_ADMIN)
    own = await client.get(f"/vendors/{vendors['Northwind Data']}/scorecard?project_id={project_id}", headers=northwind_admin)
    assert own.status_code == 200 and own.json()["vendor_name"] == "Northwind Data"
    other = await client.get(f"/vendors/{vendors['Kestrel Labs']}/scorecard?project_id={project_id}", headers=northwind_admin)
    assert other.status_code == 404
    unallocated = await client.get(f"/vendors/{vendors['Northwind Data']}/scorecard?project_id={project_id}", headers=await login(BRIGHTLINE_ADMIN))
    assert unallocated.status_code == 404
    assert (await client.get(f"/projects/{project_id}/vendors", headers=northwind_admin)).status_code == 403


async def test_vendors_see_gold_numbers_only_up_to_the_last_refresh(client, login, session, monkeypatch):
    project_id = await two_vendor_project(client, login, session)
    await rollup_project(session, project_id)
    northwind_id = await session.scalar(text("SELECT id FROM orgs WHERE name = 'Northwind Data'"))
    path = f"/vendors/{northwind_id}/scorecard?project_id={project_id}"

    as_vendor = (await client.get(path, headers=await login(NORTHWIND_ADMIN))).json()
    assert all(p["gold_accuracy"] is None for p in as_vendor["points"])  # today's work isn't refreshed yet
    assert as_vendor["alerts"] == []
    as_ops = (await client.get(path, headers=await login(DATA_OPS))).json()
    assert any(p["gold_accuracy"] == 1.0 for p in as_ops["points"])

    # A day later, the refresh has passed and the vendor sees the same gold numbers.
    real_now = datetime.now(UTC)
    monkeypatch.setattr(clock, "_anchor", (real_now, real_now + timedelta(days=1), 1))
    later = (await client.get(path, headers=await login(NORTHWIND_ADMIN))).json()
    assert [p["gold_accuracy"] for p in later["points"]] == [p["gold_accuracy"] for p in as_ops["points"]]


async def test_worker_gold_accuracy_needs_enough_gold_results(client, login, session, monkeypatch):
    project_id, generated = await make_project(client, login, tasks=50, gold_every=2, overlap=2)  # 25 gold
    await allocate(client, login, project_id, "Northwind Data", "1")
    await answer_all(client, await login(NORTHWIND_WORKER), project_id, generated, correct=True)  # 25 gold results
    first = await client.post("/assignments/next", headers=await login(NORTHWIND_WORKER_2), json={"project_id": project_id})
    await client.post(f"/assignments/{first.json()['id']}/submit", headers=await login(NORTHWIND_WORKER_2), json={"answer": {"choice": "A"}})
    real_now = datetime.now(UTC)
    monkeypatch.setattr(clock, "_anchor", (real_now, real_now + timedelta(days=1), 1))

    northwind_id = await session.scalar(text("SELECT id FROM orgs WHERE name = 'Northwind Data'"))
    rows = (await client.get(f"/vendors/{northwind_id}/workers?project_id={project_id}", headers=await login(NORTHWIND_ADMIN))).json()
    by_name = {r["display_name"]: r for r in rows}
    assert by_name[NORTHWIND_WORKER]["gold_accuracy"] == 1.0 and by_name[NORTHWIND_WORKER]["gold_n"] == 25
    assert by_name[NORTHWIND_WORKER_2]["gold_accuracy"] is None  # one gold result at most: not enough to show
    as_ops = (await client.get(f"/vendors/{northwind_id}/workers?project_id={project_id}", headers=await login(DATA_OPS))).json()
    assert all(r["display_name"] is None for r in as_ops)  # pseudonymous to Anthropic


def at(i, gold_n, gold_correct):
    return Point(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * i), 100, gold_n, gold_correct, 0, 0, 0, 0, 80, 80.0)


def test_drop_alert_fires_when_recent_gold_accuracy_falls():
    steady = [at(i, 20, 18) for i in range(8)]  # 90% on 160 gold answers
    falling = [at(8 + i, 20, 12) for i in range(4)]  # 60% on 80
    assert drop_alerts(steady) == []
    alerts = drop_alerts(steady + falling)
    assert alerts and alerts[-1]["baseline"] == 0.9 and alerts[-1]["recent"] == 0.6


def test_small_dips_are_not_alerts():
    steady = [at(i, 20, 18) for i in range(8)]
    slight = [at(8 + i, 20, 17) for i in range(4)]  # 90% -> 85%: under the 8-point bar
    assert drop_alerts(steady + slight) == []


def test_a_lucky_first_period_is_not_a_baseline():
    # Seen in the demo: one early period at 95% on 32 gold answers, then a normal 82% on 140.
    # Judged against the recent window's uncertainty alone, that looked like a 5.8-sigma drop.
    points = [at(0, 32, 30)] + [at(1 + i, 35, 29) for i in range(4)]
    assert drop_alerts(points) == []


async def test_job_queue_dedupes_and_runs_scorecards(client, login, session):
    project_id = await two_vendor_project(client, login, session)
    assert await enqueue(session, "scorecards", f"scorecards:{project_id}", {"project_id": project_id})
    assert not await enqueue(session, "scorecards", f"scorecards:{project_id}", {"project_id": project_id})
    while await run_one(session):
        pass
    done = await session.scalar(text("SELECT status FROM jobs WHERE dedupe_key = :k ORDER BY id DESC LIMIT 1"), {"k": f"scorecards:{project_id}"})
    assert done == "done"
    assert await session.scalar(text("SELECT count(*) FROM scorecard_rows WHERE project_id = :p"), {"p": project_id}) > 0
    await schedule(session)  # queues one roll-up per active project, again deduped
    assert settings.jobs_tick_seconds > 0
    assert KESTREL_ADMIN  # people module import kept for readability of the scenario
