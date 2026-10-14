"""M7 done-check: after a decided case, lineage finds everything the worker produced, clean-up
removes it, and a re-exported dataset contains none of it; the audit log shows the chain
(AB6, DC6, D14)."""

import json

from tests.builders import allocate, answer_like, make_project, report
from tests.people import DATA_OPS, NORTHWIND_ADMIN, NORTHWIND_WORKER, NORTHWIND_WORKER_2, RESEARCHER
from tests.test_abuse import PASTE


async def setup_case(client, login, session, *, overlap=1):
    """A pasting worker and an honest one; a dataset exported after detection had checked the
    answers but before it had enough of them to report anything."""
    project_id, _ = await make_project(client, login, tasks=40, overlap=overlap)
    await allocate(client, login, project_id, "Northwind Data", "1")
    bad = await answer_like(client, await login(NORTHWIND_WORKER), project_id, n=12, pasted=True, reason=PASTE)
    good = await answer_like(client, await login(NORTHWIND_WORKER_2), project_id, n=12, pasted=False, reason="Clearer steps.")
    await report(client, project_id, NORTHWIND_WORKER, rules=())  # checked, nothing found yet
    first = (await client.post(f"/projects/{project_id}/exports", headers=await login(RESEARCHER))).json()
    await report(client, project_id, NORTHWIND_WORKER, submission_ids=bad[:6])
    admin = await login(NORTHWIND_ADMIN)
    case_id = (await client.get("/cases", headers=admin)).json()[0]["id"]
    return project_id, case_id, first, bad, good


async def suspend(client, login, case_id):
    r = await client.post(f"/cases/{case_id}/decision", headers=await login(NORTHWIND_ADMIN),
                          json={"decision": "suspend", "reason": "Pasted generic answers."})
    assert r.status_code == 200


async def worker_id(client, login, project_id, name):
    users = (await client.get("/auth/demo-users")).json()
    return next(u["id"] for u in users if u["display_name"] == name)


async def test_cleanup_and_reexport_remove_every_answer_the_worker_gave(client, login, session):
    project_id, case_id, first, bad, good = await setup_case(client, login, session)
    assert first["row_count"] == 24  # before a case, both workers' answers went out
    await suspend(client, login, case_id)
    ops = await login(DATA_OPS)
    wid = await worker_id(client, login, project_id, NORTHWIND_WORKER)

    before = (await client.get(f"/lineage?worker_id={wid}&project_id={project_id}", headers=ops)).json()
    assert before["submissions"] == 12 and before["held"] == 12 and before["accepted_now"] == 0
    assert [(d["dataset_id"], d["rows_from_subject"]) for d in before["datasets"]] == [(first["id"], 12)]

    cleaned = (await client.post("/cleanups", headers=ops, json={"case_id": case_id, "mode": "relabel"})).json()
    assert cleaned["removed"] == 12 and cleaned["tasks_reopened"] == 12 and cleaned["datasets_affected"] == 1

    second = (await client.post(f"/projects/{project_id}/exports", headers=ops)).json()
    assert second["row_count"] == 12 and second["supersedes"] == first["id"]
    rows = (await client.get(f"/datasets/{second['id']}/download", headers=ops)).text.splitlines()
    exported = {json.loads(r)["submission_id"] for r in rows}
    assert exported == set(good) and not exported & set(bad)
    assert all({"submission_id", "task_id", "worker", "vendor", "schema_version", "metric_version"} <= json.loads(r).keys() for r in rows)

    after = (await client.get(f"/lineage?worker_id={wid}&project_id={project_id}", headers=ops)).json()
    assert after["removed"] == 12 and after["held"] == 0  # hold released once the answers are gone
    assert [d["current"] for d in after["datasets"]] == [False]  # only in the superseded dataset

    chain = [e["action"] for e in reversed((await client.get("/audit?limit=50", headers=ops)).json())]
    expected = ["dataset.exported", "case.opened", "case.suspend", "cleanup.done", "dataset.exported"]
    positions = [chain.index(a, chain.index(expected[i - 1]) + 1 if i else 0) for i, a in enumerate(expected)]
    assert positions == sorted(positions)


async def test_relabel_reopens_the_tasks_for_other_workers(client, login, session):
    project_id, case_id, _, bad, _ = await setup_case(client, login, session)
    await suspend(client, login, case_id)
    await client.post("/cleanups", headers=await login(DATA_OPS), json={"case_id": case_id, "mode": "relabel"})
    other = await login(NORTHWIND_WORKER_2)
    reclaimed = await answer_like(client, other, project_id, n=40, pasted=False, reason=None)
    assert len(reclaimed) == 40 - 12  # every task the honest worker hasn't answered, including the 12 reopened


async def test_discard_removes_answers_without_reopening_tasks(client, login, session):
    project_id, case_id, _, _, _ = await setup_case(client, login, session)
    await suspend(client, login, case_id)
    cleaned = (await client.post("/cleanups", headers=await login(DATA_OPS), json={"case_id": case_id, "mode": "discard"})).json()
    assert cleaned["tasks_reopened"] == 0
    other = await login(NORTHWIND_WORKER_2)
    assert len(await answer_like(client, other, project_id, n=40, pasted=False, reason=None)) == 40 - 24


async def test_removed_answers_still_count_as_delivered_but_never_accepted(client, login, session):
    project_id, case_id, _, _, _ = await setup_case(client, login, session)
    await suspend(client, login, case_id)
    ops = await login(DATA_OPS)
    await client.post("/cleanups", headers=ops, json={"case_id": case_id, "mode": "discard"})
    vendor = (await client.get(f"/projects/{project_id}/quality", headers=ops)).json()["vendors"][0]
    assert vendor["submissions"] == 24 and vendor["accepted"] == 12


async def test_cleanup_needs_a_decision_to_suspend_and_is_data_ops_only(client, login, session):
    _, case_id, _, _, _ = await setup_case(client, login, session)
    ops = await login(DATA_OPS)
    assert (await client.post("/cleanups", headers=ops, json={"case_id": case_id})).status_code == 409  # still open
    await suspend(client, login, case_id)
    assert (await client.post("/cleanups", headers=await login(NORTHWIND_ADMIN), json={"case_id": case_id})).status_code == 403
    assert (await client.post("/cleanups", headers=ops, json={"case_id": case_id})).status_code == 201
    assert (await client.post("/cleanups", headers=ops, json={"case_id": case_id})).status_code == 409  # only once
