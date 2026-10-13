"""Contracts hold raw cost inputs (VM7, PRD PD3)."""

from tests.builders import make_contract, org_ids
from tests.people import DATA_OPS, KESTREL_ADMIN, NORTHWIND_ADMIN, NORTHWIND_WORKER


async def test_data_ops_records_a_contract_and_it_is_audited(client, login):
    contract_id = await make_contract(client, login, "Kestrel Labs", price="38.00", unit="hour")
    event = (await client.get("/audit?limit=1", headers=await login(DATA_OPS))).json()[0]
    assert event["action"] == "contract.created" and event["entity_id"] == contract_id
    assert event["after"]["price"] == "38.0000" and event["after"]["price_unit"] == "hour"


async def test_price_unit_must_fit_the_kind_of_work(client, login):
    ops = await login(DATA_OPS)
    kestrel = (await org_ids(client, ops))["Kestrel Labs"]
    response = await client.post(f"/orgs/{kestrel}/contracts", headers=ops,
                                 json={"kind": "human_work", "price": "5", "price_unit": "environment", "effective_from": "2026-01-01"})
    assert response.status_code == 422


async def test_contract_needs_the_matching_capability(client, login):
    ops = await login(DATA_OPS)
    northwind = (await org_ids(client, ops))["Northwind Data"]  # human work only
    response = await client.post(f"/orgs/{northwind}/contracts", headers=ops,
                                 json={"kind": "environments", "price": "400", "price_unit": "environment", "effective_from": "2026-01-01"})
    assert response.status_code == 422


async def test_contracts_are_visible_to_their_vendor_admin_only(client, login):
    await make_contract(client, login, "Kestrel Labs")
    kestrel_id = (await org_ids(client, await login(DATA_OPS)))["Kestrel Labs"]
    assert len((await client.get(f"/orgs/{kestrel_id}/contracts", headers=await login(KESTREL_ADMIN))).json()) >= 1
    assert (await client.get(f"/orgs/{kestrel_id}/contracts", headers=await login(NORTHWIND_ADMIN))).status_code == 404
    northwind_id = (await org_ids(client, await login(DATA_OPS)))["Northwind Data"]
    assert (await client.get(f"/orgs/{northwind_id}/contracts", headers=await login(NORTHWIND_WORKER))).status_code == 403
