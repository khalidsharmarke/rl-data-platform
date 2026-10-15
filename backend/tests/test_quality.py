"""M3 done-check: seeded answers produce the expected accuracy and agreement; workers see
spot-check rejections with reasons; no worker or vendor endpoint returns gold data."""

import json

from sqlalchemy import text

from app import demo_content
from tests.builders import allocate, answer_all, make_project
from tests.people import (DATA_OPS, KESTREL_ADMIN, NORTHWIND_ADMIN, NORTHWIND_WORKER, NORTHWIND_WORKER_2,
                          RESEARCHER)


async def quality(client, login, project_id):
    return (await client.get(f"/projects/{project_id}/quality", headers=await login(DATA_OPS))).json()


async def test_gold_accuracy_and_agreement_match_the_answers_given(client, login):
    # 20 tasks, 5 gold (every 4th), 2 answers per task, all routed to Northwind.
    project_id, generated = await make_project(client, login, tasks=20, gold_every=4, overlap=2)
    await allocate(client, login, project_id, "Northwind Data", "1")
    await answer_all(client, await login(NORTHWIND_WORKER), project_id, generated, correct=True)
    await answer_all(client, await login(NORTHWIND_WORKER_2), project_id, generated, correct=False)

    q = await quality(client, login, project_id)
    assert q["metric_version"] == "quality-v1"
    workers = {w["gold_n"]: w for w in q["workers"]}
    accurate, wrong = sorted(q["workers"], key=lambda w: -(w["gold_accuracy"] or 0))
    assert (accurate["gold_n"], accurate["gold_accuracy"]) == (5, 1.0)
    assert (wrong["gold_n"], wrong["gold_accuracy"]) == (5, 0.0)
    # The two workers always disagree on the 15 regular tasks.
    assert accurate["compared"] == 15 and accurate["agreement"] == 0.0
    vendor = q["vendors"][0]
    assert vendor["submissions"] == 40 and vendor["gold_accuracy"] == 0.5
    assert workers  # both workers are reported


async def test_answers_from_workers_below_the_quality_bar_are_not_accepted(client, login):
    project_id, generated = await make_project(client, login, tasks=20, gold_every=4, overlap=2)
    await allocate(client, login, project_id, "Northwind Data", "1")
    await answer_all(client, await login(NORTHWIND_WORKER), project_id, generated, correct=True)
    await answer_all(client, await login(NORTHWIND_WORKER_2), project_id, generated, correct=False)
    accurate, wrong = sorted((await quality(client, login, project_id))["workers"], key=lambda w: -(w["gold_accuracy"] or 0))
    assert accurate["accepted"] == 15  # every regular answer
    assert wrong["accepted"] == 0  # 0% gold accuracy is below the 80% bar


async def test_rejection_needs_a_reason_and_the_worker_sees_it(client, login):
    project_id, generated = await make_project(client, login, tasks=3)
    await allocate(client, login, project_id, "Northwind Data", "1")
    worker = await login(NORTHWIND_WORKER)
    await answer_all(client, worker, project_id, generated, correct=True)
    researcher = await login(RESEARCHER)
    item = (await client.get(f"/projects/{project_id}/review-queue?n=1", headers=researcher)).json()[0]

    no_reason = await client.post(f"/submissions/{item['submission_id']}/review", headers=researcher, json={"verdict": "rejected", "looks_abusive": False})
    assert no_reason.status_code == 422
    rejected = await client.post(f"/submissions/{item['submission_id']}/review", headers=researcher,
                                 json={"verdict": "rejected", "reason": "The shorter answer is wrong, not just short.", "looks_abusive": False})
    assert rejected.status_code == 201

    feedback = (await client.get("/me/feedback", headers=worker)).json()
    assert [f["reason"] for f in feedback] == ["The shorter answer is wrong, not just short."]
    assert (await quality(client, login, project_id))["vendors"][0]["rejected"] == 1


async def test_feedback_never_reveals_gold_results(client, login):
    project_id, generated = await make_project(client, login, tasks=8, gold_every=2)
    await allocate(client, login, project_id, "Northwind Data", "1")
    worker = await login(NORTHWIND_WORKER)
    await answer_all(client, worker, project_id, generated, correct=False)  # every gold answer wrong
    assert (await client.get("/me/feedback", headers=worker)).json() == []


async def test_only_researchers_review_and_vendors_cannot_see_quality(client, login):
    project_id, _ = await make_project(client, login, tasks=2)
    await allocate(client, login, project_id, "Northwind Data", "1")
    for person in (NORTHWIND_ADMIN, NORTHWIND_WORKER):
        headers = await login(person)
        assert (await client.get(f"/projects/{project_id}/quality", headers=headers)).status_code == 403
        assert (await client.get(f"/projects/{project_id}/review-queue", headers=headers)).status_code == 403
    assert (await client.get(f"/projects/{project_id}/review-queue", headers=await login(DATA_OPS))).status_code == 403


async def test_rotation_retires_old_gold_and_routes_the_new_set_to_every_vendor(client, login, session):
    project_id, generated = await make_project(client, login, tasks=8, gold_every=4)  # 2 gold
    await allocate(client, login, project_id, "Northwind Data", "0.5")
    await allocate(client, login, project_id, "Kestrel Labs", "0.5")
    new_gold = [{"payload": g.payload | {"item": 1000 + i}, "gold_answer": {"choice": g.better}}
                for i, g in enumerate(demo_content.generate(3, seed=99))]
    response = await client.post(f"/projects/{project_id}/gold-sets", headers=await login(RESEARCHER), json={"tasks": new_gold})
    assert response.json() == {"retired": 2, "added": 3, "gold_set_version": 2}

    claimed = []
    worker = await login(NORTHWIND_WORKER)
    while (r := await client.post("/assignments/next", headers=worker, json={"project_id": project_id})).status_code == 200:
        claimed.append(r.json()["task"]["id"])
    retired = {str(t) for t in await session.scalars(text("SELECT task_id FROM gold_retirements"))}
    new_ids = {str(t) for t in await session.scalars(text(
        "SELECT g.task_id FROM gold_answers g JOIN tasks t ON t.id = g.task_id WHERE t.project_id = :p AND g.gold_set_version = 2"), {"p": project_id})}
    assert not retired & set(claimed)  # retired gold is no longer handed out
    assert new_ids <= set(claimed)  # the new set reaches this vendor
    kestrel_routes = await session.scalar(text(
        "SELECT count(*) FROM task_routes r JOIN orgs o ON o.id = r.vendor_id WHERE o.name = 'Kestrel Labs' AND r.task_id = ANY(:ids)"),
        {"ids": list(new_ids)})
    assert kestrel_routes == 3  # ...and every other vendor


async def test_no_worker_or_vendor_endpoint_returns_gold_data(client, login):
    project_id, generated = await make_project(client, login, tasks=12, gold_every=3)
    await allocate(client, login, project_id, "Northwind Data", "1")
    worker = await login(NORTHWIND_WORKER)
    done = await answer_all(client, worker, project_id, generated, correct=False)
    admin = await login(NORTHWIND_ADMIN)
    org_id = (await client.get("/orgs", headers=admin)).json()[0]["id"]

    responses = [
        await client.get("/me/feedback", headers=worker),
        await client.get("/projects", headers=worker),
        await client.get(f"/projects/{project_id}", headers=worker),
        await client.get("/projects", headers=admin),
        await client.get(f"/projects/{project_id}", headers=admin),
        await client.get("/audit", headers=admin),
        await client.get(f"/orgs/{org_id}/members", headers=admin),
        *[await client.get(f"/submissions/{d['submission_id']}/lineage", headers=admin) for d in done],
    ]
    for r in responses:
        assert r.status_code == 200, r.request.url
        body = json.dumps(r.json()).lower()
        assert "gold" not in body, r.request.url
    # A different vendor's admin can't even see the project.
    assert (await client.get(f"/projects/{project_id}", headers=await login(KESTREL_ADMIN))).status_code == 404
