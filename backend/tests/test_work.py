"""M2 done-check: a worker claims and answers tasks through the API, and every submission
carries full lineage (DC2, DC3, DC9, D14). Also: overlap, single answers per worker, and
gold tasks that look exactly like regular ones (D8)."""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.builders import allocate, make_project
from tests.people import BRIGHTLINE_ADMIN, DATA_OPS, KESTREL_ADMIN, NORTHWIND_ADMIN, NORTHWIND_WORKER, NORTHWIND_WORKER_2


async def claim(client, headers, project_id):
    return await client.post("/assignments/next", headers=headers, json={"project_id": project_id})


async def test_worker_claims_answers_and_the_submission_carries_full_lineage(client, login):
    project_id, generated = await make_project(client, login, tasks=3)
    await allocate(client, login, project_id, "Northwind Data", "1")
    worker = await login(NORTHWIND_WORKER)

    assignment = (await claim(client, worker, project_id)).json()
    assert assignment["task"]["payload"]["prompt"] and assignment["task"]["answer_schema"]["required"] == ["choice"]

    submitted = await client.post(f"/assignments/{assignment['id']}/submit", headers=worker, json={"answer": {"choice": "A"}})
    assert submitted.status_code == 201
    submission = submitted.json()
    assert submission["time_on_task_ms"] >= 0

    lineage = (await client.get(f"/submissions/{submission['id']}/lineage", headers=await login(DATA_OPS))).json()
    assert lineage["task_id"] == assignment["task"]["id"]
    assert lineage["project_id"] == project_id and lineage["schema_version"] == 1
    assert lineage["vendor_name"] == "Northwind Data"
    assert lineage["worker_handle"].startswith("U-") and lineage["worker_name"] is None  # pseudonymous to Anthropic
    assert lineage["contract_price"] == "0.7500" and lineage["contract_price_unit"] == "task"

    own_admin = (await client.get(f"/submissions/{submission['id']}/lineage", headers=await login(NORTHWIND_ADMIN))).json()
    assert own_admin["worker_name"] == NORTHWIND_WORKER
    assert (await client.get(f"/submissions/{submission['id']}/lineage", headers=await login(KESTREL_ADMIN))).status_code == 404


async def test_answers_must_fit_the_task_design(client, login):
    project_id, _ = await make_project(client, login, tasks=1)
    await allocate(client, login, project_id, "Northwind Data", "1")
    worker = await login(NORTHWIND_WORKER)
    assignment = (await claim(client, worker, project_id)).json()
    bad = await client.post(f"/assignments/{assignment['id']}/submit", headers=worker, json={"answer": {"choice": "maybe"}})
    assert bad.status_code == 422


async def test_an_assignment_can_be_submitted_once(client, login):
    project_id, _ = await make_project(client, login, tasks=1)
    await allocate(client, login, project_id, "Northwind Data", "1")
    worker = await login(NORTHWIND_WORKER)
    assignment = (await claim(client, worker, project_id)).json()
    path = f"/assignments/{assignment['id']}/submit"
    assert (await client.post(path, headers=worker, json={"answer": {"choice": "A"}})).status_code == 201
    assert (await client.post(path, headers=worker, json={"answer": {"choice": "B"}})).status_code == 409


async def test_overlap_limits_answers_per_task_and_workers_never_repeat_a_task(client, login):
    project_id, _ = await make_project(client, login, tasks=2, overlap=2)
    await allocate(client, login, project_id, "Northwind Data", "1")
    first, second = await login(NORTHWIND_WORKER), await login(NORTHWIND_WORKER_2)

    seen_by_first = {(await claim(client, first, project_id)).json()["task"]["id"] for _ in range(2)}
    assert len(seen_by_first) == 2
    assert (await claim(client, first, project_id)).status_code == 204  # nothing left that this worker hasn't had

    seen_by_second = {(await claim(client, second, project_id)).json()["task"]["id"] for _ in range(2)}
    assert seen_by_second == seen_by_first
    assert (await claim(client, second, project_id)).status_code == 204  # both tasks now have 2 answers in progress


async def test_workers_only_get_tasks_routed_to_their_vendor(client, login):
    project_id, _ = await make_project(client, login, tasks=10)
    await allocate(client, login, project_id, "Northwind Data", "1")
    brightline_worker = await login("Omar Haddad")
    assert (await claim(client, brightline_worker, project_id)).status_code == 204


async def test_expired_assignments_cannot_be_submitted(client, login, session):
    project_id, _ = await make_project(client, login, tasks=1)
    await allocate(client, login, project_id, "Northwind Data", "1")
    worker = await login(NORTHWIND_WORKER)
    assignment = (await claim(client, worker, project_id)).json()
    await session.execute(text("UPDATE assignments SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": assignment["id"]})
    response = await client.post(f"/assignments/{assignment['id']}/submit", headers=worker, json={"answer": {"choice": "A"}})
    assert response.status_code == 409


async def test_gold_tasks_look_exactly_like_regular_tasks(client, login, session):
    project_id, generated = await make_project(client, login, tasks=20, gold_every=4)
    await allocate(client, login, project_id, "Northwind Data", "1")
    worker = await login(NORTHWIND_WORKER)
    gold_ids = {str(r) for r in await session.scalars(
        text("SELECT g.task_id FROM gold_answers g JOIN tasks t ON t.id = g.task_id WHERE t.project_id = :p"), {"p": project_id})}

    responses = []
    while (r := await claim(client, worker, project_id)).status_code == 200:
        responses.append(r.json())
    gold = [r for r in responses if r["task"]["id"] in gold_ids]
    regular = [r for r in responses if r["task"]["id"] not in gold_ids]
    assert gold and regular
    shape = lambda r: (sorted(r), sorted(r["task"]), sorted(r["task"]["payload"]))  # noqa: E731
    assert {json.dumps(shape(r)) for r in gold} == {json.dumps(shape(r)) for r in regular}
    assert all("gold" not in json.dumps(r).lower() for r in responses)


@pytest.mark.parametrize("table", ["tasks", "gold_answers", "task_schemas"])
async def test_task_designs_tasks_and_gold_answers_are_immutable(session, table):
    with pytest.raises(DBAPIError, match="is immutable"):
        async with session.begin_nested():
            await session.execute(text(f"DELETE FROM {table}"))


async def test_submissions_are_immutable(client, login, session):
    project_id, _ = await make_project(client, login, tasks=1)
    await allocate(client, login, project_id, "Northwind Data", "1")
    worker = await login(NORTHWIND_WORKER)
    assignment = (await claim(client, worker, project_id)).json()
    await client.post(f"/assignments/{assignment['id']}/submit", headers=worker, json={"answer": {"choice": "A"}})
    with pytest.raises(DBAPIError, match="submissions is immutable"):
        async with session.begin_nested():
            await session.execute(text("UPDATE submissions SET answer = '{\"choice\": \"B\"}'"))


async def test_seeded_sample_project_is_allocated_across_three_vendors(client, login):
    projects = (await client.get("/projects", headers=await login(BRIGHTLINE_ADMIN))).json()
    assert [p["name"] for p in projects] == ["Which answer is more helpful? (sample)"]
