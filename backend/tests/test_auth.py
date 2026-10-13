from app.config import settings


async def test_demo_users_lists_every_seeded_role(client):
    users = (await client.get("/auth/demo-users")).json()
    roles = {u["role"] for u in users}
    assert {"researcher", "data_ops", "vendor_admin", "worker"} <= roles
    assert {u["org_name"] for u in users} >= {"Anthropic", "Northwind Data", "Kestrel Labs", "Brightline Annotation"}


async def test_demo_login_returns_a_token_that_identifies_the_user(client):
    users = (await client.get("/auth/demo-users")).json()
    admin = next(u for u in users if u["role"] == "vendor_admin" and u["org_name"] == "Kestrel Labs")

    login = await client.post("/auth/demo-login", json={"user_id": admin["id"]})
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["display_name"] == admin["display_name"]
    assert me.json()["org_kind"] == "vendor"


async def test_me_rejects_missing_and_forged_tokens(client):
    assert (await client.get("/auth/me")).status_code == 401
    forged = await client.get("/auth/me", headers={"Authorization": "Bearer not-a-token"})
    assert forged.status_code == 401


async def test_demo_logins_are_off_unless_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_logins", False)
    assert (await client.get("/auth/demo-users")).status_code == 404
