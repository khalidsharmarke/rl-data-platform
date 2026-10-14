"""M11 done-check: contributor grades (DC11, D30, D31, D32).

Quality is gold accuracy with its uncertainty; integrity is a tier that sets how much later
gold the release stage needs. A watch finding raises the bar until later gold clears it.
Grades travel with exported rows, and neither vendors nor workers can read them.
"""

import json

import pytest
from sqlalchemy import text

from app import escalation
from app.grades import LATER_GOLD, PROVEN, quality_grade, wilson
from tests.builders import allocate, make_project, report
from tests.people import DATA_OPS, NORTHWIND_ADMIN, NORTHWIND_WORKER, RESEARCHER
from tests.test_release import answer_n, verifiable


def test_the_interval_separates_little_evidence_from_a_lot():
    low, high = wilson(18, 20)
    assert round(low, 2) == 0.70 and round(high, 2) == 0.97
    assert quality_grade(18, 20, 0.8) == "uncertain"  # 90% on 20 answers
    assert quality_grade(180, 200, 0.8) == "meets"  # 90% on 200
    assert quality_grade(10, 20, 0.8) == "below"
    assert quality_grade(0, 0, 0.8) == "uncertain" and wilson(0, 0) is None


async def setup(client, login, tasks=60, **project):
    """Half the tasks are gold, so a worker passes PROVEN gold answers well within the project."""
    project.setdefault("release_cap", 10_000)
    project_id, generated = await make_project(client, login, tasks=tasks, gold_every=2, **project)
    await allocate(client, login, project_id, "Northwind Data", "1")
    return project_id, generated, await login(NORTHWIND_WORKER)


async def answer_until_gold(client, session, worker, project_id, generated, gold, **kw):
    """Answer until `gold` more gold answers are in; placement is the platform's, so count rather than assume."""
    done = []
    while sum(1 for _, g in done if g) < gold:
        done += await answer_n(client, session, worker, project_id, generated, 1, **kw)
    return done


async def grade_of(client, login, project_id):
    rows = (await client.get(f"/projects/{project_id}/grades", headers=await login(DATA_OPS))).json()["workers"]
    return next(r for r in rows if r["vendor_name"] == "Northwind Data")


async def test_a_new_worker_is_unproven_until_enough_gold(client, login, session):
    project_id, generated, worker = await setup(client, login)
    done = await answer_n(client, session, worker, project_id, generated, 10)
    g = await grade_of(client, login, project_id)
    assert g["integrity"] == "unproven" and g["later_gold_needed"] == LATER_GOLD["unproven"]
    await answer_n(client, session, worker, project_id, generated, 50)
    g = await grade_of(client, login, project_id)
    assert g["gold_n"] >= PROVEN and g["integrity"] == "trusted" and g["later_gold_needed"] == 1
    assert g["quality"] == "meets" and g["interval"][0] >= 0.8


async def test_trust_shortens_the_wait(client, login, session):
    project_id, generated, worker = await setup(client, login)
    done = await answer_n(client, session, worker, project_id, generated, 60)
    await report(client, project_id, NORTHWIND_WORKER, rules=())
    s = (await client.get(f"/projects/{project_id}/release", headers=await login(RESEARCHER))).json()
    # A trusted worker's answer needs one good gold answer after it; an unproven one needs three.
    assert s["verified"] == verifiable(done, needed=1) >= verifiable(done, needed=3)
    assert s["waiting"]["evidence"] == 30 - s["verified"]


async def test_trust_never_removes_the_minimum_gap(client, login, session):
    project_id, generated, worker = await setup(client, login, release_after=60)
    await answer_n(client, session, worker, project_id, generated, 60)
    await report(client, project_id, NORTHWIND_WORKER, rules=())
    assert (await grade_of(client, login, project_id))["integrity"] == "trusted"
    s = (await client.get(f"/projects/{project_id}/release", headers=await login(RESEARCHER))).json()
    assert s["verified"] == 0 and s["waiting"]["gap"] == 30


async def test_a_watch_finding_raises_the_bar_until_later_gold_clears_it(client, login, session):
    project_id, generated, worker = await setup(client, login, tasks=100)
    await answer_until_gold(client, session, worker, project_id, generated, PROVEN)
    assert (await grade_of(client, login, project_id))["integrity"] == "trusted"
    await report(client, project_id, NORTHWIND_WORKER, severity="watch", rules=("paste",))
    g = await grade_of(client, login, project_id)
    assert g["integrity"] == "watch" and g["later_gold_needed"] == LATER_GOLD["watch"] and g["watch_progress"] == [0, 0]
    assert (await client.get("/cases", headers=await login(NORTHWIND_ADMIN))).json() == []  # nobody at the vendor knows
    await answer_until_gold(client, session, worker, project_id, generated, 9)
    await escalation.review(session)
    assert (await grade_of(client, login, project_id))["integrity"] == "watch"  # nine aren't enough
    await answer_until_gold(client, session, worker, project_id, generated, 1)
    await escalation.review(session)
    g = await grade_of(client, login, project_id)
    assert g["integrity"] == "trusted" and g["watch_progress"] is None  # ten good gold answers cleared it


async def test_failing_gold_after_a_watch_keeps_the_worker_watched(client, login, session):
    project_id, generated, worker = await setup(client, login, tasks=100, min_gold_results=100)
    await answer_until_gold(client, session, worker, project_id, generated, PROVEN)
    await report(client, project_id, NORTHWIND_WORKER, severity="watch", rules=("paste",))
    await answer_until_gold(client, session, worker, project_id, generated, 10, correct=False)
    g = await grade_of(client, login, project_id)
    assert g["integrity"] == "watch" and g["watch_progress"][0] >= 10 and g["watch_progress"][1] == 0
    assert g["quality"] != "meets"  # half the gold is now wrong


async def test_an_open_case_makes_the_worker_held_and_a_suspension_suspended(client, login, session):
    project_id, generated, worker = await setup(client, login)
    await answer_n(client, session, worker, project_id, generated, 12)
    await report(client, project_id, NORTHWIND_WORKER)
    g = await grade_of(client, login, project_id)
    assert g["integrity"] == "held" and g["later_gold_needed"] is None
    admin = await login(NORTHWIND_ADMIN)
    case_id = (await client.get("/cases", headers=admin)).json()[0]["id"]
    await client.post(f"/cases/{case_id}/decision", headers=admin, json={"decision": "suspend", "reason": "Pasted answers."})
    await client.post("/cleanups", headers=await login(DATA_OPS), json={"case_id": case_id, "mode": "discard"})
    g = await grade_of(client, login, project_id)  # the hold is released once the answers are gone
    assert g["integrity"] == "suspended" and g["later_gold_needed"] is None


async def test_exported_rows_carry_the_workers_grade_at_release(client, login, session):
    project_id, generated, worker = await setup(client, login, release_cap=0)
    await answer_n(client, session, worker, project_id, generated, 60)
    await report(client, project_id, NORTHWIND_WORKER, rules=())
    researcher = await login(RESEARCHER)
    dataset = (await client.post(f"/projects/{project_id}/exports", headers=researcher)).json()
    rows = [json.loads(r) for r in (await client.get(f"/datasets/{dataset['id']}/download", headers=researcher)).text.splitlines()]
    assert rows and all(r["worker_grade"]["integrity"] == "trusted" and r["worker_grade"]["version"] == "grades-v1" for r in rows)
    stored = (await session.execute(text("SELECT grades FROM dataset_rows WHERE dataset_id = :d"), {"d": dataset["id"]})).scalars().all()
    assert len(stored) == len(rows) and all(g["quality"] == "meets" for g in stored)


@pytest.mark.parametrize("who", [NORTHWIND_ADMIN, NORTHWIND_WORKER])
async def test_vendors_and_workers_cannot_read_grades(client, login, session, who):
    project_id, generated, worker = await setup(client, login)
    await answer_n(client, session, worker, project_id, generated, 12)
    assert (await client.get(f"/projects/{project_id}/grades", headers=await login(who))).status_code in (403, 404)
    feedback = (await client.get("/me/feedback", headers=worker)).text
    assert "integrity" not in feedback and "grade" not in feedback
