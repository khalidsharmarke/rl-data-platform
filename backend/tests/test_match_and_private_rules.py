"""M35 done-check: the cross-vendor match, the vendor rules it enables, and vendor-private rules
(AB2, AB9, AB10, D55, D56)."""

import hashlib
import hmac
import json

from sqlalchemy import text

from app import vendor_oversight, vendor_rules
from tests.builders import DETECTION, allocate, make_project, org_ids, user_id
from tests.people import BRIGHTLINE_ADMIN, BRIGHTLINE_WORKER, DATA_OPS, NORTHWIND_ADMIN, NORTHWIND_WORKER, NORTHWIND_WORKER_2


async def keyed(client, login, admin, email):
    key = (await client.get("/match-key", headers=await login(admin))).json()
    assert key["version"] == 1 and "lowercased" in key["how"]
    return hmac.new(key["key"].encode(), email.strip().lower().encode(), hashlib.sha256).hexdigest()


async def set_identity(client, login, admin, vendor, worker, email):
    ids = await org_ids(client, await login(DATA_OPS))
    h = await keyed(client, login, admin, email)
    r = await client.put(f"/orgs/{ids[vendor]}/members/{await user_id(client, worker)}/identity", headers=await login(admin), json={"hash": h})
    assert r.status_code == 204, r.text
    return h


async def test_a_vendor_records_only_a_keyed_hash_of_its_own_workers(client, login, session):
    h = await set_identity(client, login, NORTHWIND_ADMIN, "Northwind Data", NORTHWIND_WORKER, " Tomas@Example.com ")
    assert h == await keyed(client, login, BRIGHTLINE_ADMIN, "tomas@example.com")  # same person, same hash at any vendor
    stored = await session.scalar(text("SELECT hash FROM worker_identities"))
    assert stored == h and "@" not in stored
    log = (await client.get("/audit?limit=20", headers=await login(NORTHWIND_ADMIN))).json()
    assert h not in json.dumps(log)
    ids = await org_ids(client, await login(DATA_OPS))
    other = await client.put(f"/orgs/{ids['Northwind Data']}/members/{await user_id(client, BRIGHTLINE_WORKER)}/identity",
                             headers=await login(NORTHWIND_ADMIN), json={"hash": h})
    assert other.status_code == 404
    bad = await client.put(f"/orgs/{ids['Northwind Data']}/members/{await user_id(client, NORTHWIND_WORKER)}/identity",
                           headers=await login(NORTHWIND_ADMIN), json={"hash": "tomas@example.com"})
    assert bad.status_code == 422


async def test_a_worker_suspended_at_one_vendor_returning_at_another_is_a_vendor_finding_and_nothing_more(client, login, session):
    await set_identity(client, login, BRIGHTLINE_ADMIN, "Brightline Annotation", BRIGHTLINE_WORKER, "omar@example.com")
    await set_identity(client, login, NORTHWIND_ADMIN, "Northwind Data", NORTHWIND_WORKER_2, "omar@example.com")
    assert await vendor_oversight.run(session) == 0  # a match alone is nothing
    await session.execute(text("UPDATE users SET suspended_at = now() WHERE id = :w"), {"w": await user_id(client, BRIGHTLINE_WORKER)})
    assert await vendor_oversight.run(session) == 1

    ops = await login(DATA_OPS)
    case = next(c for c in (await client.get("/cases", headers=ops)).json() if c["subject_type"] == "vendor")
    assert case["vendor_name"] == "Northwind Data" and "another vendor suspended" in case["observations"][0]
    detail = (await client.get(f"/cases/{case['id']}", headers=ops)).json()
    assert [(m["matched_vendor"], m["matched_suspended"]) for m in detail["matches"]] == [("Brightline Annotation", True)]
    # The match changes nothing on its own: no hold, and the worker still gets tasks.
    assert not await session.scalar(text("SELECT count(*) FROM holds"))
    project_id, _ = await make_project(client, login, tasks=4)
    await allocate(client, login, project_id, "Northwind Data", "1")
    assert (await client.post("/assignments/next", headers=await login(NORTHWIND_WORKER_2), json={"project_id": project_id})).status_code == 200
    # Shared with Northwind, it never says which vendor.
    await client.post(f"/cases/{case['id']}/share", headers=ops)
    seen = (await client.get(f"/cases/{case['id']}", headers=await login(NORTHWIND_ADMIN))).json()
    assert seen["matches"] == [] and "Brightline" not in json.dumps(seen)


async def test_the_same_reasons_at_two_vendors_are_a_finding_on_both(client, login, session):
    project_id, generated = await make_project(client, login, tasks=12)
    await allocate(client, login, project_id, "Northwind Data", "0.5")
    await allocate(client, login, project_id, "Brightline Annotation", "0.5")
    reason = "This answer explains the steps clearly and gives a working example to follow"
    for worker in (NORTHWIND_WORKER, BRIGHTLINE_WORKER):
        headers = await login(worker)
        for _ in range(3):
            a = (await client.post("/assignments/next", headers=headers, json={"project_id": project_id})).json()
            await client.post(f"/assignments/{a['id']}/submit", headers=headers, json={"answer": {"choice": "A", "rationale": reason}})
    assert await vendor_oversight.run(session) == 2
    vendors = {c["vendor_name"] for c in (await client.get("/cases", headers=await login(DATA_OPS))).json() if c["subject_type"] == "vendor"}
    assert vendors == {"Northwind Data", "Brightline Annotation"}


async def test_a_vendors_own_rule_opens_a_case_only_it_can_see(client, login, session):
    admin = await login(NORTHWIND_ADMIN)
    rule = await client.post("/vendor-rules", headers=admin, json={"name": "Too quick", "signal": "fast", "threshold": 60, "window": 5})
    assert rule.status_code == 201 and rule.json()["fires"] == "below"
    assert (await client.get("/vendor-rules", headers=await login(BRIGHTLINE_ADMIN))).json() == []
    project_id, _ = await make_project(client, login, tasks=10)
    await allocate(client, login, project_id, "Northwind Data", "1")
    worker = await login(NORTHWIND_WORKER)
    for _ in range(5):
        a = (await client.post("/assignments/next", headers=worker, json={"project_id": project_id})).json()
        await client.post(f"/assignments/{a['id']}/submit", headers=worker, json={"answer": {"choice": "A"}})
    assert await vendor_rules.run(session) == 1
    assert await vendor_rules.run(session) == 0  # once per rule per case

    mine = [c for c in (await client.get("/cases", headers=admin)).json() if c["private"]]
    assert len(mine) == 1 and "Your rule “Too quick”" in mine[0]["observations"][0] and not mine[0]["held"]
    ops = await login(DATA_OPS)
    assert all(not c["private"] for c in (await client.get("/cases", headers=ops)).json())
    assert (await client.get(f"/cases/{mine[0]['id']}", headers=ops)).status_code == 404
    assert (await client.get(f"/cases/{mine[0]['id']}", headers=await login(BRIGHTLINE_ADMIN))).status_code == 404
    decided = await client.post(f"/cases/{mine[0]['id']}/decision", headers=admin, json={"decision": "warn", "reason": "Talked to them"})
    assert decided.status_code == 200
    labels = (await client.get("/detection/labels", headers=DETECTION)).json()["labels"]
    assert labels == []  # a vendor's own decisions aren't detection's labels
    handling = {r["vendor_name"]: r for r in (await client.get("/vendors/case-handling", headers=ops)).json()}
    assert handling["Northwind Data"]["opened"] == 0
    assert (await client.post(f"/vendor-rules/{rule.json()['id']}/retire", headers=admin)).json()["retired_at"]
