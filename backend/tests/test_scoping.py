"""M1 done-check: a vendor can't read another vendor's data (VM1, N9)."""

from tests.people import DATA_OPS, KESTREL_ADMIN, KESTREL_WORKER, NORTHWIND_ADMIN, NORTHWIND_WORKER, RESEARCHER


async def org_id(client, headers, name):
    orgs = (await client.get("/orgs", headers=headers)).json()
    return next(o["id"] for o in orgs if o["name"] == name)


async def test_vendor_admin_cannot_read_another_vendors_workspace(client, login):
    northwind = await login(NORTHWIND_ADMIN)
    kestrel_id = await org_id(client, await login(DATA_OPS), "Kestrel Labs")

    # Another vendor's workspace doesn't appear in the list...
    assert [o["name"] for o in (await client.get("/orgs", headers=northwind)).json()] == ["Northwind Data"]
    # ...and reads by id are indistinguishable from a workspace that doesn't exist.
    assert (await client.get(f"/orgs/{kestrel_id}", headers=northwind)).status_code == 404
    assert (await client.get(f"/orgs/{kestrel_id}/members", headers=northwind)).status_code == 404
    assert (await client.get("/orgs/00000000-0000-0000-0000-000000000000", headers=northwind)).status_code == 404


async def test_vendor_admin_cannot_add_people_to_another_vendor(client, login):
    northwind = await login(NORTHWIND_ADMIN)
    kestrel_id = await org_id(client, await login(DATA_OPS), "Kestrel Labs")
    response = await client.post(f"/orgs/{kestrel_id}/members", headers=northwind, json={"display_name": "Mallory", "role": "worker"})
    assert response.status_code == 404


async def test_vendor_audit_log_shows_only_its_own_events(client, login):
    kestrel = await login(KESTREL_ADMIN)
    kestrel_id = await org_id(client, kestrel, "Kestrel Labs")
    events = (await client.get("/audit", headers=kestrel)).json()
    assert events, "seeding should have produced Kestrel events"
    assert {e["org_id"] for e in events} == {kestrel_id}


async def test_workers_are_pseudonymous_outside_their_vendor(client, login):
    kestrel_id = await org_id(client, await login(DATA_OPS), "Kestrel Labs")

    as_anthropic = (await client.get(f"/orgs/{kestrel_id}/members", headers=await login(RESEARCHER))).json()
    workers = [m for m in as_anthropic if m["role"] == "worker"]
    assert workers and all(m["display_name"] is None and m["handle"].startswith("U-") for m in workers)
    # Vendor admins are business contacts, not workers, so Anthropic sees their names.
    assert next(m for m in as_anthropic if m["role"] == "vendor_admin")["display_name"] == KESTREL_ADMIN

    as_own_admin = (await client.get(f"/orgs/{kestrel_id}/members", headers=await login(KESTREL_ADMIN))).json()
    assert KESTREL_WORKER in {m["display_name"] for m in as_own_admin}


async def test_internal_staff_see_every_workspace(client, login):
    names = {o["name"] for o in (await client.get("/orgs", headers=await login(RESEARCHER))).json()}
    assert names == {"Anthropic", "Northwind Data", "Kestrel Labs", "Brightline Annotation"}


async def test_workers_cannot_manage_members_or_read_audit(client, login):
    worker = await login(NORTHWIND_WORKER)
    own_id = await org_id(client, worker, "Northwind Data")
    assert (await client.post(f"/orgs/{own_id}/members", headers=worker, json={"display_name": "X", "role": "worker"})).status_code == 403
    assert (await client.get("/audit", headers=worker)).status_code == 403
