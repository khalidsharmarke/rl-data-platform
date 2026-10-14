"""M10 done-check: the release stage (DC10, PD7, D25, D29, D31).

Nothing unchecked or held is exported; an answer is verified only once enough gold answered
after it by the same worker meets the bar, so a worker who turns stops having earlier
answers released; the minimum gap and the cap work on the platform's clock; every exported
row carries its tier.
"""

import json
from datetime import timedelta

from sqlalchemy import text

from app import clock
from app.release import LATER_GOLD
from tests.builders import allocate, make_project, report
from tests.people import DATA_OPS, NORTHWIND_ADMIN, NORTHWIND_WORKER, RESEARCHER


async def answer_n(client, session, headers, project_id, generated, n, *, correct=True, gold_correct=None):
    """Answer n tasks in claim order. Returns [(submission_id, is_gold)]; gold placement is the platform's."""
    truth = {g.payload["item"]: g.better for g in generated}
    gold = {str(t) for t in (await session.execute(text("SELECT task_id FROM gold_answers"))).scalars()}
    done = []
    for _ in range(n):
        a = (await client.post("/assignments/next", headers=headers, json={"project_id": project_id})).json()
        is_gold = a["task"]["id"] in gold
        right = gold_correct(done) if is_gold and gold_correct else correct
        better = truth[a["task"]["payload"]["item"]]
        choice = better if right else ("B" if better == "A" else "A")
        s = await client.post(f"/assignments/{a['id']}/submit", headers=headers, json={"answer": {"choice": choice}})
        done.append((s.json()["id"], is_gold))
    return done


def verifiable(done, needed=LATER_GOLD):
    """Regular answers followed by at least `needed` gold answers (all correct here)."""
    return sum(1 for i, (_, g) in enumerate(done) if not g and sum(1 for _, g2 in done[i + 1:] if g2) >= needed)


async def setup(client, login, **project):
    """Half the tasks are gold, placed by the platform."""
    project_id, generated = await make_project(client, login, tasks=16, gold_every=2, **project)
    await allocate(client, login, project_id, "Northwind Data", "1")
    return project_id, generated, await login(NORTHWIND_WORKER)


async def summary(client, login, project_id):
    return (await client.get(f"/projects/{project_id}/release", headers=await login(RESEARCHER))).json()


async def test_nothing_is_released_before_detection_has_checked_it(client, login, session):
    project_id, generated, worker = await setup(client, login)
    done = await answer_n(client, session, worker, project_id, generated, 8)
    regular = sum(1 for _, g in done if not g)
    s = await summary(client, login, project_id)
    assert s["accepted"] == regular and s["waiting"]["detection"] == regular and s["verified"] + s["provisional"] == 0
    assert (await client.post(f"/projects/{project_id}/exports", headers=await login(RESEARCHER))).json()["row_count"] == 0
    await report(client, project_id, NORTHWIND_WORKER, rules=())  # detection has now seen them all
    s = await summary(client, login, project_id)
    assert s["waiting"]["detection"] == 0 and s["verified"] + s["provisional"] == regular


async def test_an_answer_is_verified_by_good_gold_answered_after_it(client, login, session):
    project_id, generated, worker = await setup(client, login, release_cap=10_000)
    done = await answer_n(client, session, worker, project_id, generated, 16)
    await report(client, project_id, NORTHWIND_WORKER, rules=())
    s = await summary(client, login, project_id)
    expected = verifiable(done)
    assert expected > 0 and (s["accepted"], s["verified"], s["provisional"]) == (8, expected, 0)
    assert s["waiting"]["evidence"] == 8 - expected


async def test_a_worker_whose_later_gold_fails_stops_having_earlier_answers_released(client, login, session):
    # Too few gold results to judge the worker overall (min_gold_results), so the answers are
    # accepted; but every gold answer after the first regular one is wrong.
    project_id, generated, worker = await setup(client, login, release_cap=10_000, min_gold_results=100)
    started = lambda done: not any(not g for _, g in done)  # noqa: E731  right only before any regular answer
    done = await answer_n(client, session, worker, project_id, generated, 16, gold_correct=started)
    await report(client, project_id, NORTHWIND_WORKER, rules=())
    s = await summary(client, login, project_id)
    assert verifiable(done) > 0  # had the gold been right, some would have been verified
    assert s["accepted"] == 8 and s["verified"] == 0 and s["waiting"]["evidence"] == 8


async def test_the_gap_and_the_cap_follow_the_platforms_clock(client, login, session, monkeypatch):
    project_id, generated, worker = await setup(client, login, release_after=60, release_cap=120)
    done = await answer_n(client, session, worker, project_id, generated, 16)
    await report(client, project_id, NORTHWIND_WORKER, rules=())
    assert (await summary(client, login, project_id))["waiting"]["gap"] == 8
    start = clock.now()
    monkeypatch.setattr(clock, "now", lambda: start + timedelta(minutes=61))
    s = await summary(client, login, project_id)
    assert (s["verified"], s["waiting"]["evidence"], s["provisional"]) == (verifiable(done), 8 - verifiable(done), 0)
    monkeypatch.setattr(clock, "now", lambda: start + timedelta(minutes=121))
    s = await summary(client, login, project_id)
    assert (s["verified"], s["provisional"]) == (verifiable(done), 8 - verifiable(done))


async def test_held_answers_are_never_released_even_after_the_cap(client, login, session):
    project_id, generated, worker = await setup(client, login)
    await answer_n(client, session, worker, project_id, generated, 8)
    await report(client, project_id, NORTHWIND_WORKER)  # a case: the worker's answers are held
    s = await summary(client, login, project_id)
    assert s["accepted"] == 0
    assert (await client.post(f"/projects/{project_id}/exports", headers=await login(RESEARCHER))).json()["row_count"] == 0


async def test_exported_rows_carry_their_tier(client, login, session):
    project_id, generated, worker = await setup(client, login, release_cap=0)
    done = await answer_n(client, session, worker, project_id, generated, 16)
    await report(client, project_id, NORTHWIND_WORKER, rules=())
    researcher = await login(RESEARCHER)
    dataset = (await client.post(f"/projects/{project_id}/exports", headers=researcher)).json()
    v = verifiable(done)
    assert dataset["row_count"] == 8 and dataset["provisional"] == 8 - v
    rows = [json.loads(r) for r in (await client.get(f"/datasets/{dataset['id']}/download", headers=researcher)).text.splitlines()]
    assert sorted(r["tier"] for r in rows) == ["provisional"] * (8 - v) + ["verified"] * v
    listed = (await client.get(f"/projects/{project_id}/datasets", headers=researcher)).json()[0]
    assert listed["provisional"] == 8 - v


async def test_an_answer_waits_for_its_tasks_other_answers(client, login, session):
    project_id, generated = await make_project(client, login, tasks=16, gold_every=2, overlap=2, release_cap=10_000)
    await allocate(client, login, project_id, "Northwind Data", "1")
    await answer_n(client, session, await login(NORTHWIND_WORKER), project_id, generated, 16)
    await report(client, project_id, NORTHWIND_WORKER, rules=())
    s = await summary(client, login, project_id)
    assert s["verified"] == 0 and s["waiting"]["evidence"] == 8  # nobody else has answered those tasks yet


async def test_only_anthropic_sees_what_is_released(client, login):
    project_id, _, _ = await setup(client, login)
    assert (await client.get(f"/projects/{project_id}/release", headers=await login(NORTHWIND_ADMIN))).status_code in (403, 404)
    assert (await client.get(f"/projects/{project_id}/release", headers=await login(NORTHWIND_WORKER))).status_code in (403, 404)
    assert (await client.get(f"/projects/{project_id}/release", headers=await login(DATA_OPS))).status_code == 200
