"""M24 done-check: the vendor reviewer step (DC8, D44, A29)."""

import json

from tests.builders import allocate, make_project, report
from tests.people import (DATA_OPS, KESTREL_ADMIN, NORTHWIND_ADMIN, NORTHWIND_REVIEWER, NORTHWIND_WORKER,
                          RESEARCHER)


async def reviewed_project(client, login, *, tasks=8, gold_every=2):
    """Northwind turns review on; its worker answers every task with A."""
    project_id, generated = await make_project(client, login, tasks=tasks, gold_every=gold_every)
    allocation = (await allocate(client, login, project_id, "Northwind Data", "1")).json()[0]
    switched = await client.put(f"/allocations/{allocation['id']}/vendor-review", headers=await login(NORTHWIND_ADMIN), json={"on": True})
    assert switched.status_code == 200
    worker = await login(NORTHWIND_WORKER)
    while (r := await client.post("/assignments/next", headers=worker, json={"project_id": project_id})).status_code == 200:
        await client.post(f"/assignments/{r.json()['id']}/submit", headers=worker, json={"answer": {"choice": "A", "rationale": "Clearer."}})
    return project_id, generated, allocation


async def waiting(client, login, n=50):
    return (await client.get(f"/vendor-review/queue?n={n}", headers=await login(NORTHWIND_REVIEWER))).json()


async def vendor_row(client, login, project_id):
    rows = (await client.get(f"/projects/{project_id}/quality", headers=await login(DATA_OPS))).json()["vendors"]
    return rows[0]


async def test_answers_wait_for_the_vendors_reviewer_before_reaching_anthropic(client, login, session):
    project_id, _, _ = await reviewed_project(client, login)
    queue = await waiting(client, login)
    assert queue["waiting"] == 8 and queue["items"][0]["worker_name"] == NORTHWIND_WORKER  # the vendor knows its own workers
    assert (await client.get(f"/projects/{project_id}/review-queue", headers=await login(RESEARCHER))).json() == []
    assert (await vendor_row(client, login, project_id))["accepted"] == 0
    assert (await client.get("/vendor-review/queue", headers=await login(KESTREL_ADMIN))).json()["waiting"] == 0
    # Gold looks like any other task to the reviewer (D8).
    assert len({frozenset(i) for i in queue["items"]}) == 1 and "gold" not in json.dumps(queue).lower()


async def test_a_correction_improves_what_the_vendor_submitted_but_not_the_workers_own_record(client, login, session):
    project_id, generated, _ = await reviewed_project(client, login)
    better = {g.payload["item"]: g.better for g in generated}
    reviewer = await login(NORTHWIND_REVIEWER)
    corrected = 0
    for item in (await waiting(client, login))["items"]:
        right = better[item["payload"]["item"]]
        if right == "A":
            r = await client.post(f"/vendor-review/{item['submission_id']}", headers=reviewer, json={"decision": "accepted"})
        else:
            r = await client.post(f"/vendor-review/{item['submission_id']}", headers=reviewer,
                                  json={"decision": "corrected", "answer": {"choice": right, "rationale": "Clearer."}, "note": "Wrong pick."})
            corrected += 1
        assert r.status_code == 201, r.text
    assert corrected > 0
    quality = (await client.get(f"/projects/{project_id}/quality", headers=await login(DATA_OPS))).json()
    vendor, worker = quality["vendors"][0], quality["workers"][0]
    assert vendor["gold_accuracy"] == 1.0  # every gold answer the vendor submitted is right
    assert worker["gold_accuracy"] < 1.0  # the worker's own answers keep their mistakes
    grade = next(g for g in (await client.get(f"/projects/{project_id}/grades", headers=await login(DATA_OPS))).json()["workers"])
    assert grade["gold_accuracy"] == worker["gold_accuracy"] < 1.0  # grades judge the worker's own answers
    # Exports carry what the vendor submitted; lineage keeps both versions.
    await report(client, project_id, NORTHWIND_WORKER, rules=())
    researcher = await login(RESEARCHER)
    dataset = (await client.post(f"/projects/{project_id}/exports", headers=researcher)).json()
    rows = [json.loads(r) for r in (await client.get(f"/datasets/{dataset['id']}/download", headers=researcher)).text.splitlines()]
    fixed = [r for r in rows if r["vendor_review"] and r["vendor_review"]["decision"] == "corrected"]
    assert rows and fixed and all(r["answer"]["choice"] == better[r["task"]["item"]] for r in fixed)
    lineage = (await client.get(f"/submissions/{fixed[0]['submission_id']}/lineage", headers=researcher)).json()
    assert lineage["answer"]["choice"] == "A" and lineage["vendor_review"]["corrected_answer"]["choice"] != "A"


async def test_a_correction_must_fit_the_task_and_each_answer_is_reviewed_once(client, login, session):
    await reviewed_project(client, login, tasks=2, gold_every=None)
    reviewer = await login(NORTHWIND_REVIEWER)
    item = (await waiting(client, login))["items"][0]
    url = f"/vendor-review/{item['submission_id']}"
    assert (await client.post(url, headers=reviewer, json={"decision": "corrected", "answer": {"choice": "C"}})).status_code == 422
    assert (await client.post(url, headers=reviewer, json={"decision": "corrected", "answer": item["answer"]})).status_code == 422
    assert (await client.post(url, headers=reviewer, json={"decision": "accepted"})).status_code == 201
    assert (await client.post(url, headers=reviewer, json={"decision": "accepted"})).status_code == 404
    assert (await client.post(url, headers=await login(KESTREL_ADMIN), json={"decision": "accepted"})).status_code == 404


async def test_only_the_vendors_admin_switches_review_on(client, login, session):
    project_id, _ = await make_project(client, login, tasks=2)
    allocation = (await allocate(client, login, project_id, "Northwind Data", "1")).json()[0]
    url = f"/allocations/{allocation['id']}/vendor-review"
    assert (await client.put(url, headers=await login(KESTREL_ADMIN), json={"on": True})).status_code == 404
    assert (await client.put(url, headers=await login(NORTHWIND_REVIEWER), json={"on": True})).status_code == 403
    assert (await client.put(url, headers=await login(NORTHWIND_ADMIN), json={"on": True})).json() == {"vendor_review": True}
