"""Projects, task design versions, hidden gold, allocation and routing (DC1, VP1, N19)."""

from sqlalchemy import text

from app import demo_content
from tests.builders import allocate, make_contract, make_project
from tests.people import DATA_OPS, RESEARCHER


async def test_project_rejects_an_invalid_answer_schema(client, login):
    response = await client.post("/projects", headers=await login(RESEARCHER), json={
        "name": "Broken", "task_type": "x", "answer_schema": {"type": "not-a-type"}, "instructions": "x",
        "target_volume": 10, "overlap": 1, "gold_rate": "0.05"})
    assert response.status_code == 422


async def test_gold_answer_must_fit_the_answer_schema(client, login):
    project_id, _ = await make_project(client, login, tasks=2)
    response = await client.post(f"/projects/{project_id}/tasks", headers=await login(RESEARCHER),
                                 json={"tasks": [{"payload": {"prompt": "q"}, "gold_answer": {"choice": "C"}}]})
    assert response.status_code == 422


async def test_tasks_record_their_schema_version_and_gold_is_stored_apart(client, login, session):
    project_id, _ = await make_project(client, login, tasks=40, gold_every=10)
    versions = await session.scalar(text("SELECT array_agg(DISTINCT schema_version) FROM tasks WHERE project_id = :p"), {"p": project_id})
    gold = await session.scalar(text("SELECT count(*) FROM gold_answers g JOIN tasks t ON t.id = g.task_id WHERE t.project_id = :p"), {"p": project_id})
    assert versions == [1] and gold == 4


async def test_regular_tasks_split_by_share_and_gold_goes_to_every_vendor(client, login, session):
    project_id, _ = await make_project(client, login, tasks=104, gold_every=26)  # 100 regular, 4 gold
    for vendor, share in [("Northwind Data", "0.5"), ("Kestrel Labs", "0.3"), ("Brightline Annotation", "0.2")]:
        assert (await allocate(client, login, project_id, vendor, share)).status_code == 201

    rows = (await client.get(f"/projects/{project_id}/allocations", headers=await login(DATA_OPS))).json()
    routed = {r["vendor_name"]: r["routed_tasks"] for r in rows}
    # 100 regular tasks split 50/30/20, plus all 4 gold tasks for each vendor.
    assert routed == {"Northwind Data": 54, "Kestrel Labs": 34, "Brightline Annotation": 24}


async def test_changing_a_share_moves_only_unstarted_tasks(client, login):
    project_id, _ = await make_project(client, login, tasks=100)
    await allocate(client, login, project_id, "Northwind Data", "0.5")
    response = await allocate(client, login, project_id, "Kestrel Labs", "0.5")
    kestrel = next(r for r in response.json() if r["vendor_name"] == "Kestrel Labs")
    changed = await client.patch(f"/projects/{project_id}/allocations/{kestrel['id']}", headers=await login(DATA_OPS),
                                 json={"status": "paused"})
    routed = {r["vendor_name"]: r["routed_tasks"] for r in changed.json()}
    assert routed == {"Northwind Data": 100, "Kestrel Labs": 0}


async def test_allocation_needs_the_vendors_own_human_work_contract(client, login):
    project_id, _ = await make_project(client, login, tasks=5)
    other_vendors_contract = await make_contract(client, login, "Kestrel Labs")
    response = await allocate(client, login, project_id, "Northwind Data", "0.5", contract_id=other_vendors_contract)
    assert response.status_code == 422


async def test_only_researchers_create_projects(client, login):
    response = await client.post("/projects", headers=await login(DATA_OPS), json={
        "name": "x", "task_type": demo_content.TASK_TYPE, "answer_schema": demo_content.ANSWER_SCHEMA,
        "instructions": "x", "target_volume": 1, "overlap": 1, "gold_rate": "0"})
    assert response.status_code == 403


async def test_gold_is_spread_evenly_through_each_vendors_stream(client, login, session):
    """Regular tasks must be interleaved across vendors. If each vendor got a contiguous block,
    a vendor's stream would end in a run of gold tasks, which makes gold recognizable (D8)."""
    from tests.people import NORTHWIND_WORKER

    project_id, _ = await make_project(client, login, tasks=300, gold_every=10)  # 270 regular, 30 gold
    for vendor in ("Northwind Data", "Kestrel Labs", "Brightline Annotation"):
        await allocate(client, login, project_id, vendor, "0.333")
    gold = {str(t) for t in await session.scalars(text(
        "SELECT g.task_id FROM gold_answers g JOIN tasks t ON t.id = g.task_id WHERE t.project_id = :p"), {"p": project_id})}

    worker = await login(NORTHWIND_WORKER)
    stream = []
    while (r := await client.post("/assignments/next", headers=worker, json={"project_id": project_id})).status_code == 200:
        stream.append(r.json()["task"]["id"] in gold)
    assert len(stream) == 120 and sum(stream) == 30  # 90 regular + every gold task
    quarter = len(stream) // 4
    # Evenly mixed, each quarter holds about a quarter of the gold (7-8 of 30).
    assert all(sum(stream[i * quarter:(i + 1) * quarter]) <= 12 for i in range(4)), stream
