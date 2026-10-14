"""M6 done-check, through the detection contract (M9): a case-severity finding opens a case
with a hold for the vendor admin, who decides; every action links to its case (AB3, AB4,
AB7, AB8, D13, D16, N17, N18). The rules themselves live in the detection service
(detection/tests)."""

import json

from sqlalchemy import text

from tests.builders import allocate, answer_like, make_project, report
from tests.people import DATA_OPS, KESTREL_ADMIN, NORTHWIND_ADMIN, NORTHWIND_WORKER, NORTHWIND_WORKER_2

PASTE = "Answer A is more helpful because it directly addresses the user's question with accurate, actionable detail."


async def flagged_case(client, login, session):
    """One worker pastes the same reason; another works honestly. Detection reports the first."""
    project_id, _ = await make_project(client, login, tasks=30)
    await allocate(client, login, project_id, "Northwind Data", "1")
    bad = await answer_like(client, await login(NORTHWIND_WORKER), project_id, n=12, pasted=True, reason=PASTE)
    await answer_like(client, await login(NORTHWIND_WORKER_2), project_id, n=12, pasted=False, reason="Clearer steps.")
    await report(client, project_id, NORTHWIND_WORKER, submission_ids=bad[:6])
    return project_id


async def test_a_case_finding_opens_one_case_with_a_hold_for_the_vendor_admin(client, login, session):
    project_id = await flagged_case(client, login, session)
    cases = (await client.get("/cases", headers=await login(NORTHWIND_ADMIN))).json()
    assert len(cases) == 1
    case = cases[0]
    assert case["subject_name"] == NORTHWIND_WORKER and case["owner"] == "vendor_admin" and case["held"]
    assert any("pasted" in o for o in case["observations"])
    await report(client, project_id, NORTHWIND_WORKER)  # reporting again joins the open case
    assert len((await client.get("/cases", headers=await login(NORTHWIND_ADMIN))).json()) == 1


async def test_held_answers_are_not_accepted(client, login, session):
    project_id = await flagged_case(client, login, session)
    q = (await client.get(f"/projects/{project_id}/quality", headers=await login(DATA_OPS))).json()
    handles = {m["id"]: m for m in q["workers"]}
    accepted = sorted(w["accepted"] for w in handles.values())
    assert accepted == [0, 12]  # the flagged worker's 12 answers are held


async def test_vendors_see_observations_not_rules_and_other_vendors_see_nothing(client, login, session):
    await flagged_case(client, login, session)
    admin = await login(NORTHWIND_ADMIN)
    case_id = (await client.get("/cases", headers=admin)).json()[0]["id"]
    detail = (await client.get(f"/cases/{case_id}", headers=admin)).json()
    assert detail["flags"] and all(f["rule"] is None and f["rule_version"] is None for f in detail["flags"])
    assert "gold" not in json.dumps(detail).lower() and "threshold" not in json.dumps(detail).lower()
    assert detail["samples"] and detail["actions"][0]["action"] == "opened"
    as_ops = (await client.get(f"/cases/{case_id}", headers=await login(DATA_OPS))).json()
    assert {f["rule"] for f in as_ops["flags"]} >= {"paste"} and as_ops["subject_name"] is None
    assert (await client.get(f"/cases/{case_id}", headers=await login(KESTREL_ADMIN))).status_code == 404
    assert (await client.get("/cases", headers=await login(KESTREL_ADMIN))).json() == []


async def test_only_the_owner_decides_and_a_reason_is_required(client, login, session):
    await flagged_case(client, login, session)
    case_id = (await client.get("/cases", headers=await login(DATA_OPS))).json()[0]["id"]
    assert (await client.post(f"/cases/{case_id}/decision", headers=await login(DATA_OPS), json={"decision": "suspend", "reason": "x" * 5})).status_code == 403
    assert (await client.post(f"/cases/{case_id}/decision", headers=await login(NORTHWIND_ADMIN), json={"decision": "suspend"})).status_code == 422


async def test_suspension_blocks_work_and_every_action_links_to_the_case(client, login, session):
    project_id = await flagged_case(client, login, session)
    admin = await login(NORTHWIND_ADMIN)
    case_id = (await client.get("/cases", headers=admin)).json()[0]["id"]
    decided = (await client.post(f"/cases/{case_id}/decision", headers=admin,
                                 json={"decision": "suspend", "reason": "Pasted generic answers without reading the tasks."})).json()
    assert decided["status"] == "decided" and decided["held"]  # data stays held for clean-up
    worker = await login(NORTHWIND_WORKER)
    assert (await client.post("/assignments/next", headers=worker, json={"project_id": project_id})).status_code == 403

    actions = [a["action"] for a in decided["actions"]]
    assert actions == ["opened", "hold", "suspend"]
    audit = (await client.get("/audit", headers=admin)).json()
    assert any(e["action"] == "case.suspend" and e["entity_id"] == case_id for e in audit)
    orphan = await session.scalar(text("SELECT count(*) FROM case_actions a LEFT JOIN cases c ON c.id = a.case_id WHERE c.id IS NULL"))
    assert orphan == 0


async def test_a_decision_can_be_reversed(client, login, session):
    project_id = await flagged_case(client, login, session)
    admin = await login(NORTHWIND_ADMIN)
    case_id = (await client.get("/cases", headers=admin)).json()[0]["id"]
    await client.post(f"/cases/{case_id}/decision", headers=admin, json={"decision": "suspend", "reason": "Pasted answers."})
    reversed_ = (await client.post(f"/cases/{case_id}/reverse", headers=await login(DATA_OPS), json={"reason": "Pasting was a browser extension; reviewed."})).json()
    assert not reversed_["held"] and reversed_["decision"] == "suspend"  # the original decision stays on record
    assert [a["action"] for a in reversed_["actions"]][-1] == "reverse"
    worker = await login(NORTHWIND_WORKER)
    assert (await client.post("/assignments/next", headers=worker, json={"project_id": project_id})).status_code == 200


async def test_clearing_releases_the_hold(client, login, session):
    project_id = await flagged_case(client, login, session)
    admin = await login(NORTHWIND_ADMIN)
    case_id = (await client.get("/cases", headers=admin)).json()[0]["id"]
    cleared = (await client.post(f"/cases/{case_id}/decision", headers=admin, json={"decision": "clear", "reason": "Checked: legitimate."})).json()
    assert not cleared["held"]
    q = (await client.get(f"/projects/{project_id}/quality", headers=await login(DATA_OPS))).json()
    assert sorted(w["accepted"] for w in q["workers"]) == [12, 12]
