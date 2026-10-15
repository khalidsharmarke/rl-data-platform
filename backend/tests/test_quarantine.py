"""M16 done-check, platform side: environment cases, quarantine, lineage of pulls, and
environment work on the scorecard (TO6, AB3, AB6, AB8, D13, D16, VP3, N22)."""

from tests.people import BRIGHTLINE_ADMIN, DATA_OPS, KESTREL_ADMIN, RESEARCHER
from tests.test_review_catalog import TRAINING, in_review, review, validate
from tests.test_validation import fake, submitted

BENCHMARK = "A researcher noticed this task copies a public benchmark problem word for word."


async def approved_and_pulled(client, login, session, **kw):
    project_id, version_id, builder = await in_review(client, login, session, **kw)
    await review(client, login, version_id)
    await client.post(f"/catalog/{version_id}/pull", headers=TRAINING, json={"run_label": "run-1"})
    return project_id, version_id, builder


async def open_case(client, login, version_id):
    return await client.post("/cases/environment", headers=await login(DATA_OPS), json={"version_id": version_id, "observation": BENCHMARK})


async def test_a_case_quarantines_the_version_and_lists_who_pulled_it(client, login, session):
    _, version_id, builder = await approved_and_pulled(client, login, session)
    opened = await open_case(client, login, version_id)
    assert opened.status_code == 201, opened.text
    case = opened.json()
    assert case["owner"] == "data_ops" and case["held"] and case["by_person"] and case["subject_type"] == "environment_version"
    assert case["environment"]["status"] == "quarantined"
    assert [(p["puller"], p["run_label"]) for p in case["environment"]["pulls"]] == [("training", "run-1")]
    assert [a["action"] for a in case["actions"]] == ["opened", "hold", "recall"]  # training pulled it, so a recall goes out
    assert (await client.get("/catalog", headers=TRAINING)).json() == []  # out of the catalog at once
    assert (await client.get(f"/versions/{version_id}", headers=builder)).json()["status"] == "quarantined"
    # The vendor sees its own environment's case; other vendors don't.
    assert [c["id"] for c in (await client.get("/cases", headers=await login(KESTREL_ADMIN))).json()] == [case["id"]]
    assert (await client.get(f"/cases/{case['id']}", headers=await login(BRIGHTLINE_ADMIN))).status_code == 404


async def test_only_data_ops_opens_and_decides_and_only_approved_versions_are_quarantined(client, login, session):
    _, in_review_id, _ = await in_review(client, login, session)
    assert (await open_case(client, login, in_review_id)).status_code == 409
    await review(client, login, in_review_id)
    attempt = await client.post("/cases/environment", headers=await login(KESTREL_ADMIN), json={"version_id": in_review_id, "observation": BENCHMARK})
    assert attempt.status_code == 403
    case_id = (await open_case(client, login, in_review_id)).json()["id"]
    assert (await open_case(client, login, in_review_id)).status_code == 409  # already quarantined
    decide = lambda who, d: client.post(f"/cases/{case_id}/decision", headers=who, json={"decision": d, "reason": "Checked the source."})  # noqa: E731
    assert (await decide(await login(KESTREL_ADMIN), "clear")).status_code == 403
    assert (await decide(await login(DATA_OPS), "suspend")).status_code == 422  # not a decision for an environment


async def test_remove_keeps_it_quarantined_and_clear_sends_it_back_to_review(client, login, session):
    project_id, removed, builder = await approved_and_pulled(client, login, session, name="copied")
    _, cleared, _ = await approved_and_pulled(client, login, session, project_id=project_id, name="fine")
    ops = await login(DATA_OPS)
    for version_id, decision in ((removed, "remove"), (cleared, "clear")):
        case_id = (await open_case(client, login, version_id)).json()["id"]
        r = await client.post(f"/cases/{case_id}/decision", headers=ops, json={"decision": decision, "reason": "Compared with the benchmark."})
        assert r.status_code == 200, r.text
    assert (await client.get(f"/versions/{removed}", headers=builder)).json()["status"] == "quarantined"
    assert (await client.get(f"/versions/{cleared}", headers=builder)).json()["status"] == "in_review"
    assert (await client.get("/catalog", headers=TRAINING)).json() == []
    # The cleared version can be approved again, and only then returns to the catalog.
    await review(client, login, cleared)
    assert [e["version_id"] for e in (await client.get("/catalog", headers=TRAINING)).json()] == [cleared]
    # Clean-up is for workers' answers; an environment case can't be cleaned up that way.
    removed_case = next(c for c in (await client.get("/cases", headers=ops)).json() if c["decision"] == "remove")
    assert (await client.post("/cleanups", headers=ops, json={"case_id": removed_case["id"]})).status_code == 409


async def test_environment_work_appears_on_the_vendor_scorecard(client, login, session):
    project_id, approved, _ = await approved_and_pulled(client, login, session, name="good")
    await submitted(client, login, project_id=project_id, name="bad", fixture="passes_anything")
    await validate(session, fake({"reference": True, "empty": False, "repeat": True}))
    _, sent_back, _ = await submitted(client, login, project_id=project_id, name="meh")
    await validate(session, fake({"reference": True, "empty": True, "repeat": True}))
    await client.post(f"/versions/{sent_back}/reviews", headers=await login(RESEARCHER),
                      json={"decision": "send_back", "rubric": {c: False for c in (await client.get(f"/projects/{project_id}", headers=await login(RESEARCHER))).json()["brief"]["review_rubric"]}, "feedback": "Too easy."})
    await open_case(client, login, approved)
    [row] = (await client.get(f"/vendors/environment-work?project_id={project_id}", headers=await login(DATA_OPS))).json()
    assert row["vendor_name"] == "Kestrel Labs"
    assert (row["submitted"], row["checked"], row["passed_checks"]) == (3, 3, 2)
    assert (row["reviewed"], row["approved"], row["sent_back"], row["quarantined"]) == (2, 1, 1, 1)
    assert abs(row["check_pass_rate"] - 2 / 3) < 1e-9 and row["approval_rate"] == 0.5
    assert (await client.get("/vendors/environment-work", headers=await login(BRIGHTLINE_ADMIN))).json() == []
    assert len((await client.get("/vendors/environment-work", headers=await login(KESTREL_ADMIN))).json()) == 1
