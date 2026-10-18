"""M27 done-check: self-serve onboarding (VM1, VM3, N14, D46).

A new vendor joins through a single-use invite, proves its setup on samples, and only then can
be allocated real work. The checklist is computed from what happened; the samples run on the
real path and feed nothing: no scorecard, dataset, review queue or catalog.
"""

from datetime import timedelta

from sqlalchemy import text

from app import clock, demo_content, onboarding, sandbox
from app.config import settings
from app.jobs import schedule
from tests import envfixtures
from tests.builders import answer_all, make_project
from tests.people import DATA_OPS, KESTREL_ADMIN, RESEARCHER
from tests.test_tokens import bearer
from tests.test_validation import fake

SAMPLES = demo_content.generate(onboarding.SAMPLE_COUNT, seed=101)


async def new_vendor(client, login, name="Harbor Labs", capabilities=("human_work", "environments")) -> tuple[str, dict]:
    ops = await login(DATA_OPS)
    org = await client.post("/orgs", headers=ops, json={"name": name, "capabilities": list(capabilities)})
    assert org.status_code == 201, org.text
    invite = await client.post(f"/orgs/{org.json()['id']}/invites", headers=ops, json={"display_name": "Ines Duarte"})
    assert invite.status_code == 201, invite.text
    return org.json()["id"], invite.json()


async def join(client, invite) -> dict[str, str]:
    r = await client.post("/auth/invites/accept", json={"code": invite["code"]})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "vendor_admin"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def checklist(client, headers, org_id) -> dict[str, dict]:
    r = await client.get(f"/orgs/{org_id}/onboarding", headers=headers)
    assert r.status_code == 200, r.text
    return {i["key"]: i for i in r.json()["items"]} | {"_": r.json()}


async def onboard(client, login, session, org_id, admin) -> None:
    """Do everything the checklist asks: a worker answers the samples, a token submits a sample that passes."""
    worker = await client.post(f"/orgs/{org_id}/members", headers=admin, json={"display_name": "Kai Mbeki", "role": "worker"})
    assert worker.status_code == 201, worker.text
    projects = {p["name"]: p["id"] for p in (await client.get("/projects", headers=admin)).json()}
    await answer_all(client, await login("Kai Mbeki"), projects[onboarding.SAMPLE_TASKS_PROJECT], SAMPLES, correct=True)

    token = (await client.post("/tokens", headers=admin, json={"name": "ci", "scopes": ["environments", "read"]})).json()
    env = (await client.post("/environments", headers=bearer(token),
                             json={"project_id": projects[onboarding.SAMPLE_BRIEF], "name": "sample"})).json()
    version = (await client.post(f"/environments/{env['id']}/versions", headers=bearer(token), json=envfixtures.version_body("sound"))).json()
    await client.post(f"/versions/{version['id']}/submit", headers=bearer(token))
    while (job := await sandbox.claim(session)) is not None:
        await sandbox.process(session, job, checks=fake({"reference": True, "empty": True, "repeat": True}))


async def test_an_invite_signs_the_admin_in_once_and_expires(client, login, session, monkeypatch):
    org_id, invite = await new_vendor(client, login)
    _, late = await new_vendor(client, login, name="Late Labs")
    monkeypatch.setattr(settings, "demo_logins", False)  # invites are a real way in, not a demo login
    admin = await join(client, invite)
    assert (await client.get("/auth/me", headers=admin)).json()["org_id"] == org_id
    assert (await client.post("/auth/invites/accept", json={"code": invite["code"]})).status_code == 404
    await session.execute(text("UPDATE invites SET expires_at = :t WHERE display_name = 'Ines Duarte' AND accepted_at IS NULL"),
                          {"t": clock.now() - timedelta(seconds=1)})
    assert (await client.post("/auth/invites/accept", json={"code": late["code"]})).status_code == 404


async def test_real_work_waits_for_a_complete_checklist(client, login, session):
    org_id, invite = await new_vendor(client, login)
    admin = await join(client, invite)
    items = await checklist(client, admin, org_id)
    assert items["admin"]["done"] and not any(items[k]["done"] for k in ("worker", "samples", "token", "sample_environment"))

    project_id, _ = await make_project(client, login, tasks=10)
    ops = await login(DATA_OPS)
    contract = (await client.post(f"/orgs/{org_id}/contracts", headers=ops, json={
        "kind": "human_work", "price": "0.70", "price_unit": "task", "effective_from": "2026-01-01"})).json()
    allocation = {"vendor_id": org_id, "contract_id": contract["id"], "share": "1"}
    refused = await client.post(f"/projects/{project_id}/allocations", headers=ops, json=allocation)
    assert refused.status_code == 409 and "onboarding" in refused.text

    await onboard(client, login, session, org_id, admin)
    items = await checklist(client, ops, org_id)
    assert all(i["done"] for k, i in items.items() if k != "_"), items
    assert items["_"]["complete"] and items["_"]["onboarded_at"] and items["_"]["first_accepted_at"]
    assert (await client.post(f"/projects/{project_id}/allocations", headers=ops, json=allocation)).status_code == 201
    overview = (await client.get("/onboarding", headers=ops)).json()
    assert [v["org_name"] for v in overview] == ["Harbor Labs"] and overview[0]["invited_at"]


async def test_wrong_sample_answers_dont_count(client, login):
    org_id, invite = await new_vendor(client, login, capabilities=("human_work",))
    admin = await join(client, invite)
    await client.post(f"/orgs/{org_id}/members", headers=admin, json={"display_name": "Kai Mbeki", "role": "worker"})
    projects = {p["name"]: p["id"] for p in (await client.get("/projects", headers=admin)).json()}
    await answer_all(client, await login("Kai Mbeki"), projects[onboarding.SAMPLE_TASKS_PROJECT], SAMPLES, correct=False)
    item = (await checklist(client, admin, org_id))["samples"]
    assert not item["done"] and "below the bar" in item["detail"]


async def test_samples_feed_nothing(client, login, session):
    org_id, invite = await new_vendor(client, login)
    admin = await join(client, invite)
    await onboard(client, login, session, org_id, admin)
    projects = {p["name"]: p for p in (await client.get("/projects", headers=await login(DATA_OPS))).json()}
    tasks, brief = projects[onboarding.SAMPLE_TASKS_PROJECT], projects[onboarding.SAMPLE_BRIEF]
    assert tasks["onboarding"] and brief["onboarding"]

    researcher = await login(RESEARCHER)
    assert (await client.post(f"/projects/{tasks['id']}/exports", headers=researcher)).status_code == 422
    await schedule(session)
    assert not await session.scalar(text("SELECT count(*) FROM jobs WHERE dedupe_key = :k"), {"k": f"scorecards:{tasks['id']}"})
    # The passing sample is in review, but no researcher sees it or can approve it into the catalog.
    queue = (await client.get("/versions?status=in_review", headers=researcher)).json()
    assert all(v["project_id"] != brief["id"] for v in queue)
    sample = (await client.get(f"/versions?project_id={brief['id']}", headers=researcher)).json()[0]
    review = await client.post(f"/versions/{sample['id']}/reviews", headers=researcher,
                               json={"decision": "approve", "rubric": {"Passes the sandbox's checks": True}, "feedback": "Looks fine"})
    assert review.status_code == 409
    work = (await client.get("/vendors/environment-work", headers=researcher)).json()
    assert "Harbor Labs" not in {r["vendor_name"] for r in work}


async def test_the_checklist_is_the_vendors_own_and_readable_by_its_tooling(client, login):
    org_id, invite = await new_vendor(client, login)
    admin = await join(client, invite)
    token = (await client.post("/tokens", headers=admin, json={"name": "ci", "scopes": ["read"]})).json()
    mine = (await client.get("/orgs", headers=bearer(token))).json()
    assert [o["id"] for o in mine] == [org_id]
    assert (await client.get(f"/orgs/{org_id}/onboarding", headers=bearer(token))).status_code == 200
    assert (await client.get(f"/orgs/{org_id}/onboarding", headers=await login(KESTREL_ADMIN))).status_code == 404
