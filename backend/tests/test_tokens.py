"""M25 done-check: vendor API tokens (VM2, N11, D45).

A token belongs to its vendor org, reaches only that vendor's data and its scopes, never acts
for a person, is shown once and stored hashed, and stops working when revoked or expired.
"""

from datetime import timedelta

from sqlalchemy import text

from app import clock
from tests.builders import org_ids
from tests.people import DATA_OPS, KESTREL_ADMIN, NORTHWIND_ADMIN
from tests.test_environments import BUILDER, allocate_brief, bundle, make_brief


async def make_token(client, login, admin=KESTREL_ADMIN, name="ci", scopes=("environments", "read")):
    r = await client.post("/tokens", headers=await login(admin), json={"name": name, "scopes": list(scopes)})
    assert r.status_code == 201, r.text
    return r.json()


def bearer(token) -> dict[str, str]:
    return {"Authorization": f"Bearer {token['secret']}"}


async def test_a_token_is_shown_once_and_stored_only_as_a_hash(client, login, session):
    token = await make_token(client, login)
    assert token["secret"].startswith("rlp1_") and token["prefix"] == token["secret"][:12]
    listed = (await client.get("/tokens", headers=await login(KESTREL_ADMIN))).json()
    assert [t["name"] for t in listed] == ["ci"] and "secret" not in listed[0]
    stored = await session.scalar(text("SELECT secret_hash FROM api_tokens WHERE id = :i"), {"i": token["id"]})
    assert token["secret"] not in stored


async def test_a_token_submits_environments_for_its_vendor_attributed_to_the_token(client, login, session):
    brief = await make_brief(client, login)
    assert (await allocate_brief(client, login, brief)).status_code == 201
    token = await make_token(client, login)
    env = await client.post("/environments", headers=bearer(token), json={"project_id": brief, "name": "slugify"})
    assert env.status_code == 201, env.text
    version = await client.post(f"/environments/{env.json()['id']}/versions", headers=bearer(token), json=bundle())
    assert version.status_code == 201, version.text
    assert (await client.post(f"/versions/{version.json()['id']}/submit", headers=bearer(token))).status_code == 200

    row = (await session.execute(text("SELECT created_by, created_by_token FROM environment_versions WHERE id = :v"),
                                 {"v": version.json()["id"]})).one()
    assert row.created_by is None and str(row.created_by_token) == token["id"]
    audit = (await client.get(f"/audit?token_id={token['id']}", headers=await login(KESTREL_ADMIN))).json()
    assert {e["action"] for e in audit} >= {"environment.created", "environment_version.created", "environment_version.submitted"}
    assert all(e["actor_id"] is None for e in audit)


async def test_a_token_reaches_only_its_vendors_data_and_its_scopes(client, login):
    ops = await login(DATA_OPS)
    ids = await org_ids(client, ops)
    reader = await make_token(client, login, name="reports", scopes=["read"])
    project = (await client.get("/projects", headers=bearer(reader))).json()[0]["id"]
    assert (await client.get(f"/vendors/{ids['Kestrel Labs']}/scorecard?project_id={project}", headers=bearer(reader))).status_code == 200
    # Another vendor's data looks like it doesn't exist.
    assert (await client.get(f"/vendors/{ids['Northwind Data']}/scorecard?project_id={project}", headers=bearer(reader))).status_code == 404
    # A read token can't write, even on routes tokens may use.
    brief = await make_brief(client, login)
    await allocate_brief(client, login, brief)
    r = await client.post("/environments", headers=bearer(reader), json={"project_id": brief, "name": "x"})
    assert r.status_code == 403


async def test_a_token_never_does_what_a_person_must(client, login):
    token = await make_token(client, login)
    ids = await org_ids(client, await login(DATA_OPS))
    for method, path in [("POST", "/assignments/next"), ("POST", f"/orgs/{ids['Kestrel Labs']}/members"),
                         ("POST", "/tokens"), ("GET", "/tokens"), ("GET", "/auth/me"), ("GET", "/audit")]:
        r = await client.request(method, path, headers=bearer(token), json={"name": "x", "scopes": ["read"], "display_name": "x", "role": "worker"})
        assert r.status_code == 403, (method, path, r.status_code)
    case = await client.post("/cases/00000000-0000-0000-0000-000000000000/decision", headers=bearer(token), json={})
    assert case.status_code == 403


async def test_a_revoked_or_expired_token_stops_working(client, login, session):
    token = await make_token(client, login)
    assert (await client.get("/environments", headers=bearer(token))).status_code == 200
    # Data ops can revoke a vendor's token, for instance after a leak.
    assert (await client.post(f"/tokens/{token['id']}/revoke", headers=await login(DATA_OPS))).status_code == 200
    assert (await client.get("/environments", headers=bearer(token))).status_code == 401

    other = await make_token(client, login, name="ci-2")
    await session.execute(text("UPDATE api_tokens SET expires_at = :t WHERE id = :i"),
                          {"t": clock.now() - timedelta(seconds=1), "i": other["id"]})
    assert (await client.get("/environments", headers=bearer(other))).status_code == 401


async def test_last_use_is_recorded_and_tokens_are_private_to_their_vendor(client, login):
    token = await make_token(client, login)
    await client.get("/environments", headers=bearer(token))
    mine = (await client.get("/tokens", headers=await login(KESTREL_ADMIN))).json()[0]
    assert mine["last_used_at"] is not None
    assert (await client.get("/tokens", headers=await login(NORTHWIND_ADMIN))).json() == []
    assert (await client.post(f"/tokens/{token['id']}/revoke", headers=await login(NORTHWIND_ADMIN))).status_code == 404
    assert [t["id"] for t in (await client.get("/tokens", headers=await login(DATA_OPS))).json() if t["org_name"] == "Kestrel Labs"] == [token["id"]]
