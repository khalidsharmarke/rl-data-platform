"""M9 done-check, platform side: the detection contract (AB13, D23, D24, D25, D32).

Every submission writes one event with its raw signals; only the detection service can read
events or report findings; a watch finding is recorded without a case; progress only moves
forward; decisions flow back as labels. Commit ordering of events is tested with real
transactions in test_concurrency.py; the rules are tested in detection/tests.
"""

from sqlalchemy import text

from tests.builders import DETECTION, allocate, answer_like, make_project, report, user_id
from tests.people import DATA_OPS, NORTHWIND_ADMIN, NORTHWIND_WORKER
from tests.test_abuse import PASTE


async def project_with_answers(client, login, n=12):
    project_id, _ = await make_project(client, login, tasks=30)
    await allocate(client, login, project_id, "Northwind Data", "1")
    ids = await answer_like(client, await login(NORTHWIND_WORKER), project_id, n=n, pasted=True, reason=PASTE)
    return project_id, ids


async def test_every_submission_publishes_one_event_with_its_raw_signals(client, login):
    project_id, ids = await project_with_answers(client, login)
    events = (await client.get("/detection/events", headers=DETECTION)).json()["events"]
    ours = [e for e in events if e["project_id"] == project_id]
    assert [e["submission_id"] for e in ours] == ids  # one each, in order
    first = ours[0]
    assert first["worker_id"] == await user_id(client, NORTHWIND_WORKER)
    assert first["client_signals"] == {"pasted": True} and first["answer"]["rationale"] == PASTE
    assert first["task_words"] > 20 and first["time_on_task_ms"] >= 0
    later = (await client.get("/detection/events", headers=DETECTION, params={"after": ours[5]["id"]})).json()["events"]
    assert [e["submission_id"] for e in later if e["project_id"] == project_id] == ids[6:]


async def test_only_the_detection_service_can_use_the_contract(client, login):
    project_id, _ = await project_with_answers(client, login, n=1)
    ops = await login(DATA_OPS)
    for path in ("/detection/events", "/detection/progress", "/detection/labels"):
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, headers=ops)).status_code == 401
    body = {"detector": "rules-v1", "processed_through": 1, "findings": []}
    assert (await client.post("/detection/findings", headers=ops, json=body)).status_code == 401


async def test_a_watch_finding_is_recorded_without_a_case_or_a_hold(client, login, session):
    project_id, ids = await project_with_answers(client, login)
    result = await report(client, project_id, NORTHWIND_WORKER, severity="watch", rules=("paste",))
    assert result == {"cases_opened": 0}
    assert (await client.get("/cases", headers=await login(NORTHWIND_ADMIN))).json() == []
    assert (await client.get("/cases", headers=await login(DATA_OPS))).json() == []
    rows = (await session.execute(text("SELECT severity, case_id FROM findings WHERE project_id = :p"), {"p": project_id})).all()
    assert [(r.severity, r.case_id) for r in rows] == [("watch", None)]
    holds = await session.scalar(text("SELECT count(*) FROM holds WHERE worker_id = :w"), {"w": await user_id(client, NORTHWIND_WORKER)})
    assert holds == 0


async def test_a_case_finding_links_to_its_case_and_repeats_are_ignored(client, login, session):
    project_id, ids = await project_with_answers(client, login)
    assert (await report(client, project_id, NORTHWIND_WORKER))["cases_opened"] == 1
    await report(client, project_id, NORTHWIND_WORKER)  # delivered again after a restart
    rows = (await session.execute(text("SELECT rule, case_id FROM findings WHERE project_id = :p"), {"p": project_id})).all()
    assert len(rows) == 3 and len({r.case_id for r in rows}) == 1 and rows[0].case_id is not None


async def test_progress_only_moves_forward(client, login):
    project_id, _ = await project_with_answers(client, login, n=3)
    await report(client, project_id, NORTHWIND_WORKER, severity="watch", rules=(), processed_through=50)
    await report(client, project_id, NORTHWIND_WORKER, severity="watch", rules=(), processed_through=20)
    assert (await client.get("/detection/progress", headers=DETECTION)).json()["processed_through"] == 50


async def test_decisions_flow_back_as_labels_covering_the_answers_before_them(client, login):
    project_id, ids = await project_with_answers(client, login)
    await report(client, project_id, NORTHWIND_WORKER)
    admin = await login(NORTHWIND_ADMIN)
    case_id = (await client.get("/cases", headers=admin)).json()[0]["id"]
    await client.post(f"/cases/{case_id}/decision", headers=admin, json={"decision": "clear", "reason": "Checked: legitimate."})
    labels = (await client.get("/detection/labels", headers=DETECTION)).json()["labels"]
    events = (await client.get("/detection/events", headers=DETECTION, params={"limit": 2000})).json()["events"]
    assert labels[-1]["decision"] == "clear" and labels[-1]["worker_id"] == await user_id(client, NORTHWIND_WORKER)
    assert labels[-1]["through_event"] == events[-1]["id"]
    after = (await client.get("/detection/labels", headers=DETECTION, params={"after": labels[-1]["seq"]})).json()
    assert after["labels"] == []
