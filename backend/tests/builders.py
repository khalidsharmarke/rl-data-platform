"""Helpers that set up projects, contracts and allocations through the API."""

from app import demo_content
from tests.people import DATA_OPS, RESEARCHER


async def org_ids(client, headers) -> dict[str, str]:
    return {o["name"]: o["id"] for o in (await client.get("/orgs", headers=headers)).json()}


async def make_project(client, login, *, tasks: int, gold_every: int | None = None, overlap: int = 1) -> tuple[str, list]:
    researcher = await login(RESEARCHER)
    project = (
        await client.post(
            "/projects",
            headers=researcher,
            json={
                "name": f"Test project {tasks}/{gold_every}/{overlap}",
                "task_type": demo_content.TASK_TYPE,
                "answer_schema": demo_content.ANSWER_SCHEMA,
                "instructions": demo_content.INSTRUCTIONS,
                "target_volume": tasks * overlap,
                "overlap": overlap,
                "gold_rate": "0.05",
            },
        )
    ).json()
    generated = demo_content.generate(tasks, seed=11)
    body = [
        {"payload": g.payload, **({"gold_answer": {"choice": g.better}} if gold_every and i % gold_every == 0 else {})}
        for i, g in enumerate(generated)
    ]
    response = await client.post(f"/projects/{project['id']}/tasks", headers=researcher, json={"tasks": body})
    assert response.status_code == 201, response.text
    return project["id"], generated


async def make_contract(client, login, vendor: str, price="0.75", unit="task") -> str:
    ops = await login(DATA_OPS)
    vendor_id = (await org_ids(client, ops))[vendor]
    response = await client.post(
        f"/orgs/{vendor_id}/contracts", headers=ops,
        json={"kind": "human_work", "price": price, "price_unit": unit, "effective_from": "2026-01-01"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def allocate(client, login, project_id: str, vendor: str, share: str, contract_id: str | None = None):
    ops = await login(DATA_OPS)
    vendor_id = (await org_ids(client, ops))[vendor]
    contract_id = contract_id or await make_contract(client, login, vendor)
    return await client.post(
        f"/projects/{project_id}/allocations", headers=ops,
        json={"vendor_id": vendor_id, "contract_id": contract_id, "share": share},
    )
