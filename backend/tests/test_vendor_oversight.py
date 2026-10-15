"""M23 done-check: vendor rules, vendor cases shared with the vendor, its response, and
actions on the vendor (AB2, AB5, AB7, D13, D42)."""

from datetime import timedelta

from sqlalchemy import text

from app import clock, vendor_oversight
from tests.builders import allocate, make_project, org_ids
from tests.people import BRIGHTLINE_ADMIN, BRIGHTLINE_WORKER, DATA_OPS, KESTREL_ADMIN, NORTHWIND_ADMIN
from tests.test_quarantine import approved_and_pulled, open_case

REASON = "Accuracy fell while volume rose; the vendor's answer didn't explain it."


async def seed_periods(session, project_id, vendor_id, *, before=(0.9, 20), recent=(0.6, 30)):
    """Sixteen scorecard periods: eight like `before`, then eight like `recent` (accuracy, answers)."""
    start = clock.now() - timedelta(hours=16)
    for i in range(16):
        accuracy, volume = before if i < 8 else recent
        await session.execute(text(
            "INSERT INTO scorecard_rows (metric_version, project_id, vendor_id, period_start, submissions, gold_n, gold_correct, "
            "compared, agreed, reviewed, rejected, accepted, time_on_task_ms, turnaround_s, cost, computed_at) "
            "VALUES ('quality-v2', :p, :v, :t, :s, 10, :g, 0, 0, 0, 0, 0, 0, 0, 0, now())"),
            {"p": project_id, "v": vendor_id, "t": start + timedelta(hours=i), "s": volume, "g": round(accuracy * 10)})


async def brightline_falling(client, login, session):
    project_id, _ = await make_project(client, login, tasks=40)
    await allocate(client, login, project_id, "Northwind Data", "0.6")
    await allocate(client, login, project_id, "Brightline Annotation", "0.4")
    vendor_id = (await org_ids(client, await login(DATA_OPS)))["Brightline Annotation"]
    await seed_periods(session, project_id, vendor_id)
    assert await vendor_oversight.run(session) == 1
    return project_id, vendor_id


async def allocations_of(session, vendor):
    rows = await session.execute(text("SELECT a.project_id, a.status FROM allocations a JOIN orgs o ON o.id = a.vendor_id "
                                      "WHERE o.name = :n"), {"n": vendor})
    return [{"project_id": str(r.project_id), "status": r.status} for r in rows]


async def vendor_case(client, login):
    return next(c for c in (await client.get("/cases", headers=await login(DATA_OPS))).json() if c["subject_type"] == "vendor")


async def test_quality_falling_while_volume_rises_opens_a_case_only_anthropic_sees(client, login, session):
    await brightline_falling(client, login, session)
    case = await vendor_case(client, login)
    assert case["owner"] == "data_ops" and case["subject_handle"] == "Brightline Annotation" and case["shared_at"] is None
    assert "fell from 90% to 60%" in case["observations"][0]
    assert (await client.get("/cases", headers=await login(BRIGHTLINE_ADMIN))).json() == []  # not shared yet
    assert (await client.get(f"/cases/{case['id']}", headers=await login(BRIGHTLINE_ADMIN))).status_code == 404
    assert await vendor_oversight.run(session) == 0  # the same finding isn't recorded twice


async def test_falling_quality_with_falling_volume_is_not_this_rule(client, login, session):
    project_id, _ = await make_project(client, login, tasks=40)
    await allocate(client, login, project_id, "Brightline Annotation", "1")
    vendor_id = (await org_ids(client, await login(DATA_OPS)))["Brightline Annotation"]
    await seed_periods(session, project_id, vendor_id, recent=(0.6, 10))
    assert await vendor_oversight.run(session) == 0


async def test_actions_wait_for_the_vendors_response_then_routing_follows(client, login, session):
    project_id, vendor_id = await brightline_falling(client, login, session)
    case_id = (await vendor_case(client, login))["id"]
    ops, admin = await login(DATA_OPS), await login(BRIGHTLINE_ADMIN)
    lower = {"action": "lower_allocation", "project_id": project_id, "share": "0.2", "reason": REASON}
    assert (await client.post(f"/cases/{case_id}/actions", headers=ops, json=lower)).status_code == 409  # not shared yet
    shared = await client.post(f"/cases/{case_id}/share", headers=ops)
    assert shared.status_code == 200 and not shared.json()["can_act"]
    seen = (await client.get(f"/cases/{case_id}", headers=admin)).json()
    assert all(f["rule"] is None for f in seen["flags"]) and seen["respond_by"]  # observations, never rules (N17)
    assert (await client.post(f"/cases/{case_id}/actions", headers=ops, json=lower)).status_code == 409  # no response yet
    assert (await client.post(f"/cases/{case_id}/responses", headers=await login(KESTREL_ADMIN),
                              json={"text": "Not ours."})).status_code == 404
    reply = await client.post(f"/cases/{case_id}/responses", headers=admin,
                              json={"text": "One worker caused it; we suspended them and retrained the team."})
    assert reply.status_code == 201
    acted = await client.post(f"/cases/{case_id}/actions", headers=ops, json=lower)
    assert acted.status_code == 200, acted.text
    shares = {a["vendor_name"]: a["share"] for a in (await client.get(f"/projects/{project_id}/allocations", headers=ops)).json()}
    assert float(shares["Brightline Annotation"]) == 0.2
    assert (await client.post(f"/cases/{case_id}/actions", headers=ops, json=lower)).status_code == 422  # not lower
    # Pause: the vendor's workers get no new tasks.
    await client.post(f"/cases/{case_id}/actions", headers=ops, json={"action": "pause", "reason": REASON})
    assert (await client.post("/assignments/next", headers=await login(BRIGHTLINE_WORKER), json={"project_id": project_id})).status_code == 204
    resolved = await client.post(f"/cases/{case_id}/decision", headers=ops, json={"decision": "resolve", "reason": REASON})
    assert resolved.json()["status"] == "decided"
    history = [a["action"] for a in resolved.json()["actions"]]
    assert history == ["opened", "shared", "response", "lower_allocation", "pause", "resolve"]
    log = (await client.get("/audit", headers=admin)).json()
    assert {"vendor.lower_allocation", "vendor.pause"} <= {e["action"] for e in log}  # the vendor sees what was done to it


async def test_without_a_response_actions_wait_for_the_window(client, login, session, monkeypatch):
    await brightline_falling(client, login, session)
    case_id = (await vendor_case(client, login))["id"]
    ops = await login(DATA_OPS)
    await client.post(f"/cases/{case_id}/share", headers=ops)
    pause = {"action": "pause", "reason": REASON}
    assert (await client.post(f"/cases/{case_id}/actions", headers=ops, json=pause)).status_code == 409
    later = clock.now() + vendor_oversight.RESPONSE_WINDOW + timedelta(minutes=1)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert (await client.post(f"/cases/{case_id}/actions", headers=ops, json=pause)).status_code == 200


async def test_a_cleared_vendor_case_isnt_reopened_on_the_same_rule_straight_away(client, login, session):
    await brightline_falling(client, login, session)
    case_id = (await vendor_case(client, login))["id"]
    ops = await login(DATA_OPS)
    assert (await client.post(f"/cases/{case_id}/decision", headers=ops,
                              json={"decision": "resolve", "reason": REASON})).status_code == 409  # no action taken
    await client.post(f"/cases/{case_id}/decision", headers=ops, json={"decision": "clear", "reason": "Explained by a hard batch."})
    assert await vendor_oversight.run(session) == 0


async def test_quarantined_environments_and_case_handling_are_vendor_rules(client, login, session):
    project_id, first, _ = await approved_and_pulled(client, login, session, name="one")
    _, second, _ = await approved_and_pulled(client, login, session, project_id=project_id, name="two")
    for version_id in (first, second):
        await open_case(client, login, version_id)
    northwind = (await org_ids(client, await login(DATA_OPS)))["Northwind Data"]
    for _ in range(4):  # four worker cases, all cleared
        await session.execute(text(
            "INSERT INTO cases (subject_type, subject_id, vendor_id, owner, status, decision, opened_at, decided_at) "
            "SELECT 'worker', u.id, u.org_id, 'vendor_admin', 'decided', 'clear', now(), now() FROM users u "
            "WHERE u.org_id = :v AND u.role = 'worker' LIMIT 1"),
            {"v": northwind})
    assert await vendor_oversight.run(session) == 2
    rules = {(c["subject_handle"], c["observations"][0].split(":")[0]) for c in (await client.get("/cases", headers=await login(DATA_OPS))).json()
             if c["subject_type"] == "vendor"}
    assert ("Kestrel Labs", "2 of the vendor's environment versions were quarantined in the last 30 days.") in rules
    assert ("Northwind Data", "How the vendor handles its own cases") in rules
    # Both quarantined versions belong to one brief, so the case names it, and data ops can pause the vendor on just that brief.
    kestrel = next(c for c in (await client.get("/cases", headers=await login(DATA_OPS))).json()
                   if c["subject_type"] == "vendor" and c["subject_handle"] == "Kestrel Labs")
    ops = await login(DATA_OPS)
    detail = (await client.post(f"/cases/{kestrel['id']}/share", headers=ops)).json()
    assert detail["project_id"] == project_id
    await client.post(f"/cases/{kestrel['id']}/responses", headers=await login(KESTREL_ADMIN), json={"text": "Retired both."})
    paused = await client.post(f"/cases/{kestrel['id']}/actions", headers=ops,
                               json={"action": "pause", "project_id": project_id, "reason": "Until graders get a second review."})
    assert paused.status_code == 200, paused.text
    statuses = {(a["project_id"], a["status"]) for a in await allocations_of(session, "Kestrel Labs")}
    assert (project_id, "paused") in statuses and any(s == "active" for _, s in statuses)  # only that brief
    handling = {r["vendor_name"]: r for r in (await client.get("/vendors/case-handling", headers=await login(DATA_OPS))).json()}
    assert (handling["Northwind Data"]["decided"], handling["Northwind Data"]["cleared"]) == (4, 4)
    assert (await client.get("/vendors/case-handling", headers=await login(NORTHWIND_ADMIN))).status_code == 403
