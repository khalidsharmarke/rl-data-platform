"""M12 done-check: discreet escalation (AB14, D26, D32).

A watch finding makes the platform measure the worker more closely, invisibly: more of their
tasks are gold, and their tasks look exactly like everyone else's. Ten later gold answers
then clear the finding or confirm it as a case. The vendor learns nothing until a case.
"""

import json

import pytest
from sqlalchemy import text

from app import escalation
from tests.builders import allocate, make_project, report
from tests.people import DATA_OPS, NORTHWIND_ADMIN, NORTHWIND_WORKER, NORTHWIND_WORKER_2
from tests.test_grades import grade_of
from tests.test_release import answer_n


@pytest.fixture
def always_gold_first(monkeypatch):
    monkeypatch.setattr(escalation, "ESCALATED_GOLD_SHARE", 1.0)


async def watched(client, login, session):
    """A worker with a watch finding: one signal on its own (pasting)."""
    project_id, generated = await make_project(client, login, tasks=100, gold_every=2, release_cap=10_000)
    await allocate(client, login, project_id, "Northwind Data", "1")
    worker = await login(NORTHWIND_WORKER)
    await answer_n(client, session, worker, project_id, generated, 10)
    await report(client, project_id, NORTHWIND_WORKER, severity="watch", rules=("paste",))
    return project_id, generated, worker


async def escalations(client, login, project_id):
    return (await client.get(f"/projects/{project_id}/escalations", headers=await login(DATA_OPS))).json()


async def test_a_watch_finding_opens_an_escalation_nobody_at_the_vendor_can_see(client, login, session):
    project_id, _, _ = await watched(client, login, session)
    [x] = await escalations(client, login, project_id)
    assert x["rule"] == "paste" and x["outcome"] is None
    assert (await grade_of(client, login, project_id))["integrity"] == "watch"
    admin = await login(NORTHWIND_ADMIN)
    assert (await client.get("/cases", headers=admin)).json() == []
    vendor_log = json.dumps((await client.get("/audit?limit=200", headers=admin)).json())
    assert "escalation" not in vendor_log
    ops_log = [e["action"] for e in (await client.get("/audit?limit=200", headers=await login(DATA_OPS))).json()]
    assert "escalation.started" in ops_log
    assert (await client.get(f"/projects/{project_id}/escalations", headers=admin)).status_code in (403, 404)


async def test_an_escalated_worker_gets_gold_first_in_tasks_that_look_like_anyone_elses(client, login, session, always_gold_first):
    project_id, generated, worker = await watched(client, login, session)
    gold = {str(t) for t in (await session.execute(text("SELECT task_id FROM gold_answers"))).scalars()}
    other = await login(NORTHWIND_WORKER_2)
    mine = (await client.post("/assignments/next", headers=worker, json={"project_id": project_id})).json()
    theirs = (await client.post("/assignments/next", headers=other, json={"project_id": project_id})).json()
    assert mine["task"]["id"] in gold
    # Same shape, same fields; nothing says why this task came next.
    assert mine.keys() == theirs.keys() and mine["task"].keys() == theirs["task"].keys()
    assert "gold" not in json.dumps(mine).lower() and "escalat" not in json.dumps(mine).lower()
    done = await answer_n(client, session, worker, project_id, generated, 9)
    assert all(g for _, g in done)  # every claim while escalated tried gold first


async def test_good_later_gold_clears_the_escalation(client, login, session, always_gold_first):
    project_id, generated, worker = await watched(client, login, session)
    await answer_n(client, session, worker, project_id, generated, 10)
    assert await escalation.review(session) == {"cleared": 1, "confirmed": 0}
    [x] = await escalations(client, login, project_id)
    assert x["outcome"] == "cleared" and x["gold_since"] == 10 and x["gold_right_since"] == 10
    assert (await grade_of(client, login, project_id))["integrity"] != "watch"
    assert (await client.get("/cases", headers=await login(NORTHWIND_ADMIN))).json() == []


async def test_failing_later_gold_confirms_it_as_a_case_without_revealing_gold(client, login, session, always_gold_first):
    project_id, generated, worker = await watched(client, login, session)
    await answer_n(client, session, worker, project_id, generated, 9, correct=False)
    assert await escalation.review(session) == {"cleared": 0, "confirmed": 0}  # nine: not enough yet
    await answer_n(client, session, worker, project_id, generated, 1, correct=False)
    assert await escalation.review(session) == {"cleared": 0, "confirmed": 1}
    [x] = await escalations(client, login, project_id)
    assert x["outcome"] == "confirmed"
    admin = await login(NORTHWIND_ADMIN)
    [case] = (await client.get("/cases", headers=admin)).json()
    assert case["held"] and "quality bar" in case["observations"][0]
    detail = json.dumps((await client.get(f"/cases/{case['id']}", headers=admin)).json()).lower()
    assert "gold" not in detail and "test question" not in detail and "escalat" not in detail
    assert (await grade_of(client, login, project_id))["integrity"] == "held"


async def test_below_the_bar_but_not_clearly_keeps_watching(client, login, session, always_gold_first):
    # 9 of 13 right is 69%, under the 80% bar, but the 95% interval reaches 87%: an honest,
    # careful worker can be that unlucky. Keep measuring; at 30 answers, clear as inconclusive.
    project_id, generated, worker = await watched(client, login, session)
    await answer_n(client, session, worker, project_id, generated, 9)
    await answer_n(client, session, worker, project_id, generated, 4, correct=False)
    assert await escalation.review(session) == {"cleared": 0, "confirmed": 0}
    assert (await escalations(client, login, project_id))[0]["outcome"] is None
    await answer_n(client, session, worker, project_id, generated, 12)
    await answer_n(client, session, worker, project_id, generated, 5, correct=False)  # 21 of 30: 70%, interval up to 83%
    assert await escalation.review(session) == {"cleared": 1, "confirmed": 0}
    assert (await client.get("/cases", headers=await login(NORTHWIND_ADMIN))).json() == []


async def test_a_case_finding_ends_the_escalation(client, login, session):
    project_id, _, _ = await watched(client, login, session)
    await report(client, project_id, NORTHWIND_WORKER)
    [x] = await escalations(client, login, project_id)
    assert x["outcome"] == "case"


async def test_no_escalation_while_a_case_already_holds_the_worker(client, login, session):
    project_id, generated = await make_project(client, login, tasks=40, gold_every=2)
    await allocate(client, login, project_id, "Northwind Data", "1")
    await answer_n(client, session, await login(NORTHWIND_WORKER), project_id, generated, 10)
    await report(client, project_id, NORTHWIND_WORKER)
    await report(client, project_id, NORTHWIND_WORKER, severity="watch", rules=("speed",), processed_through=10_000)
    assert await escalations(client, login, project_id) == []
