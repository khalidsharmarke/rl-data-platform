"""Workspaces, capabilities and members (VM1, VM6), and their audit events (VM4)."""

from tests.people import BRIGHTLINE_ADMIN, DATA_OPS, RESEARCHER


async def latest_event(client, headers):
    return (await client.get("/audit?limit=1", headers=headers)).json()[0]


async def test_data_ops_creates_a_vendor_workspace_and_it_is_audited(client, login):
    ops = await login(DATA_OPS)
    response = await client.post("/orgs", headers=ops, json={"name": "Harbor Experts", "capabilities": ["human_work"], "domains": ["law"]})
    assert response.status_code == 201
    org = response.json()
    assert org["kind"] == "vendor" and org["capabilities"] == ["human_work"]

    event = await latest_event(client, ops)
    assert event["action"] == "org.created" and event["entity_id"] == org["id"]
    assert event["after"]["domains"] == ["law"]


async def test_only_data_ops_creates_workspaces(client, login):
    response = await client.post("/orgs", headers=await login(RESEARCHER), json={"name": "Nope", "capabilities": ["human_work"]})
    assert response.status_code == 403


async def test_unknown_capability_is_rejected(client, login):
    response = await client.post("/orgs", headers=await login(DATA_OPS), json={"name": "Odd", "capabilities": ["telepathy"]})
    assert response.status_code == 422


async def test_changing_capabilities_records_before_and_after(client, login):
    ops = await login(DATA_OPS)
    brightline = next(o for o in (await client.get("/orgs", headers=ops)).json() if o["name"] == "Brightline Annotation")
    response = await client.put(f"/orgs/{brightline['id']}/capabilities", headers=ops, json={"capabilities": ["human_work", "environments"]})
    assert response.json()["capabilities"] == ["environments", "human_work"]

    event = await latest_event(client, ops)
    assert event["action"] == "org.capabilities_set"
    assert event["before"]["capabilities"] == ["human_work"]
    assert event["after"]["capabilities"] == ["environments", "human_work"]


async def test_vendor_admin_adds_a_worker_with_a_fresh_handle(client, login):
    admin = await login(BRIGHTLINE_ADMIN)
    own = (await client.get("/orgs", headers=admin)).json()[0]
    response = await client.post(f"/orgs/{own['id']}/members", headers=admin, json={"display_name": "Ines Duarte", "role": "worker"})
    assert response.status_code == 201
    member = response.json()
    assert member["display_name"] == "Ines Duarte" and member["handle"].startswith("U-")

    event = await latest_event(client, admin)
    assert event["action"] == "user.added" and event["org_id"] == own["id"]


async def test_vendor_admin_cannot_create_anthropic_roles(client, login):
    admin = await login(BRIGHTLINE_ADMIN)
    own = (await client.get("/orgs", headers=admin)).json()[0]
    response = await client.post(f"/orgs/{own['id']}/members", headers=admin, json={"display_name": "Eve", "role": "data_ops"})
    assert response.status_code == 422
