"""M19 done-check, platform side: hack findings, the recall and training's reports (TO3, TO6,
TO8, D35, D37, D38).

A case-severity finding quarantines the version without a person and issues a recall naming
the run and where flagged attempts began; training reads it and reports, and the report lands
on the case. A watch finding asks a researcher to read transcripts instead. Only the detector
and training can use their sides of the contract.
"""

from sqlalchemy import text

from tests import envfixtures
from tests.builders import DETECTION
from tests.people import DATA_OPS, KESTREL_ADMIN, RESEARCHER
from tests.test_review_catalog import TRAINING, review, validate
from tests.test_validation import fake, submitted


async def weak_approved(client, login, session, name="slugify edge cases", project_id=None):
    project_id, version_id, _ = await submitted(client, login, name=name, fixture="weak_grader", project_id=project_id)
    await validate(session, fake({"reference": True, "empty": True, "repeat": True}))
    await review(client, login, version_id)
    await client.post(f"/catalog/{version_id}/pull", headers=TRAINING, json={"run_label": "rl-run-002"})
    return project_id, version_id


async def train(client, version_id, run="rl-run-002", n=40, hack_from=20):
    eps = [{"episode_id": f"{run}-{version_id[:6]}-{i}", "version_id": version_id, "step": 1000 + i * 100,
            "reward": 1.0 if i >= hack_from else 0.0, "passed": i >= hack_from, "length": 4 if i >= hack_from else 40,
            "touched": ["task/slug.py"], "transcript": "edit task/slug.py: return \"\"" if i >= hack_from else "honest attempt"}
           for i in range(n)]
    r = await client.post("/ingest/episodes", headers=TRAINING, json={"run_label": run, "episodes": eps})
    assert r.status_code == 200, r.text


def finding(version_id, severity="case", rule="shortcut", run="rl-run-002", first=3000):
    return {"version_id": version_id, "run_label": run, "rule": rule, "rule_version": f"{rule}-v1", "severity": severity,
            "observation": "Full marks in a tenth of the usual length.",
            "evidence": {"episode_ids": [f"{run}-{version_id[:6]}-30", f"{run}-{version_id[:6]}-31"], "flagged_episodes": 20,
                         "first_flagged_step": first}}


async def report_findings(client, *findings, through=10_000):
    r = await client.post("/hack-detection/findings", headers=DETECTION,
                          json={"detector": "hacks-v1", "processed_through": through, "findings": list(findings)})
    assert r.status_code == 202, r.text
    return r.json()


async def test_a_case_finding_quarantines_without_a_person_and_issues_a_recall(client, login, session):
    _, version_id = await weak_approved(client, login, session)
    await train(client, version_id)
    assert await report_findings(client, finding(version_id)) == {"cases_opened": 1}
    assert (await client.get("/catalog", headers=TRAINING)).json() == []
    [case] = (await client.get("/cases", headers=await login(DATA_OPS))).json()
    assert not case["by_person"]
    detail = (await client.get(f"/cases/{case['id']}", headers=await login(DATA_OPS))).json()
    assert detail["environment"]["status"] == "quarantined" and detail["owner"] == "data_ops"
    assert [a["action"] for a in detail["actions"]] == ["opened", "hold", "recall"] and detail["actions"][0]["by"] == "Platform"
    assert detail["flags"][0]["rule"] == "shortcut"
    [recall] = (await client.get("/recalls", headers=TRAINING)).json()
    [run] = recall["runs"]
    assert (run["run_label"], run["first_step"], run["episodes"], run["first_flagged_step"], run["flagged"]) == ("rl-run-002", 1000, 40, 3000, 20)
    assert (await client.get(f"/cases/{case['id']}/recall", headers=await login(RESEARCHER))).json()["id"] == recall["id"]


async def test_flagged_counts_are_running_totals_not_added_up(client, login, session):
    # A watch finding at 8 flagged attempts, then the case finding at 20: the recall says 20.
    _, version_id = await weak_approved(client, login, session)
    await train(client, version_id)
    watch = finding(version_id, severity="watch")
    watch["evidence"] = {**watch["evidence"], "flagged_episodes": 8}
    await report_findings(client, watch, finding(version_id))
    [recall] = (await client.get("/recalls", headers=TRAINING)).json()
    assert recall["runs"][0]["flagged"] == 20


async def test_training_reports_what_it_did_and_the_case_records_it(client, login, session):
    _, version_id = await weak_approved(client, login, session)
    await train(client, version_id)
    await report_findings(client, finding(version_id))
    [recall] = (await client.get("/recalls", headers=TRAINING)).json()
    url = f"/recalls/{recall['id']}/reports"
    assert (await client.post(url, headers=TRAINING, json={"run_label": "rl-run-002", "action": "rolled_back"})).status_code == 422
    assert (await client.post(url, headers=TRAINING, json={"run_label": "rl-run-999", "action": "continued"})).status_code == 422
    ok = await client.post(url, headers=TRAINING, json={"run_label": "rl-run-002", "action": "rolled_back", "checkpoint_step": 2500})
    assert ok.status_code == 201 and ok.json()["reports"][0]["checkpoint_step"] == 2500
    assert (await client.post(url, headers=TRAINING, json={"run_label": "rl-run-002", "action": "continued"})).status_code == 409
    [case] = (await client.get("/cases", headers=await login(DATA_OPS))).json()
    actions = (await client.get(f"/cases/{case['id']}", headers=await login(DATA_OPS))).json()["actions"]
    assert actions[-1]["action"] == "report" and "step 2,500" in actions[-1]["note"]


async def test_a_watch_finding_asks_a_researcher_to_read_transcripts(client, login, session):
    _, version_id = await weak_approved(client, login, session)
    await train(client, version_id)
    assert await report_findings(client, finding(version_id, severity="watch", rule="jump")) == {"cases_opened": 0}
    assert (await client.get("/cases", headers=await login(DATA_OPS))).json() == []
    assert len((await client.get("/catalog", headers=TRAINING)).json()) == 1  # still in the catalog
    [check] = (await client.get("/hack-checks", headers=await login(RESEARCHER))).json()
    assert check["environment_name"] == "slugify edge cases" and check["episodes"][0]["transcript"].startswith("edit task/slug.py")
    assert (await client.post(f"/hack-checks/{check['id']}", headers=await login(RESEARCHER), json={"outcome": "looks_fine"})).status_code == 200
    assert (await client.get("/hack-checks", headers=await login(RESEARCHER))).json() == []


async def test_a_watch_check_leaves_the_queue_once_its_version_is_quarantined(client, login, session):
    # The detector watches first, then opens a case: the case is now where the version is decided.
    _, version_id = await weak_approved(client, login, session)
    await train(client, version_id)
    await report_findings(client, finding(version_id, severity="watch"))
    assert len((await client.get("/hack-checks", headers=await login(RESEARCHER))).json()) == 1
    await report_findings(client, finding(version_id))
    assert (await client.get("/hack-checks", headers=await login(RESEARCHER))).json() == []


async def test_repeats_are_ignored_and_a_second_rule_joins_the_case(client, login, session):
    _, version_id = await weak_approved(client, login, session)
    await train(client, version_id)
    await report_findings(client, finding(version_id))
    assert await report_findings(client, finding(version_id)) == {"cases_opened": 0}
    await report_findings(client, finding(version_id, rule="touched-grader"))
    [case] = (await client.get("/cases", headers=await login(DATA_OPS))).json()
    detail = (await client.get(f"/cases/{case['id']}", headers=await login(DATA_OPS))).json()
    assert {f["rule"] for f in detail["flags"]} == {"shortcut", "touched-grader"}
    assert [a["action"] for a in detail["actions"]] == ["opened", "hold", "recall", "flag"]  # the repeat added nothing


async def test_a_version_no_run_received_gets_no_recall(client, login, session):
    project_id, version_id, _ = await submitted(client, login, fixture="sound")
    await validate(session, fake({"reference": True, "empty": True, "repeat": True}))
    await review(client, login, version_id)
    r = await client.post("/cases/environment", headers=await login(DATA_OPS), json={"version_id": version_id, "observation": "Copied task."})
    assert [a["action"] for a in r.json()["actions"]] == ["opened", "hold"]
    assert (await client.get("/recalls", headers=TRAINING)).json() == []


async def test_only_the_detector_and_training_use_their_sides(client, login, session):
    _, version_id = await weak_approved(client, login, session)
    await train(client, version_id, n=3)
    feed = (await client.get("/hack-detection/episodes", headers=DETECTION)).json()["episodes"]
    assert [e["step"] for e in feed] == [1000, 1100, 1200] and feed[0]["run_label"] == "rl-run-002"
    ops = await login(DATA_OPS)
    assert (await client.get("/hack-detection/episodes", headers=ops)).status_code == 401
    assert (await client.post("/hack-detection/findings", headers=TRAINING, json={"detector": "x", "processed_through": 1})).status_code == 401
    assert (await client.get("/recalls", headers=DETECTION)).status_code == 401
    assert (await client.get("/hack-checks", headers=await login(KESTREL_ADMIN))).status_code == 403
