"""M15 done-check: the builder's loop, researcher review and the catalog (EC2, EC4, EC5, EC6,
EC8, N21, N22).

A builder sees why a check failed and fixes it in a new version, with a file-by-file diff;
a researcher grades against the brief's rubric; only approved versions reach the catalog,
which gives training the task files and grader but never the reference solution, and records
every pull.
"""

import base64

from sqlalchemy import text

from app import sandbox
from tests import envfixtures
from tests.people import BRIGHTLINE_ADMIN, DATA_OPS, KESTREL_ADMIN, RESEARCHER
from tests.test_environments import BRIEF
from tests.test_validation import fake, submitted

TRAINING = {"Authorization": "Bearer test-training-token"}
ALL_MET = {c: True for c in BRIEF["review_rubric"]}


async def validate(session, checks):
    while (job := await sandbox.claim(session)) is not None:
        await sandbox.process(session, job, checks=checks)


async def in_review(client, login, session, **kw):
    project_id, version_id, builder = await submitted(client, login, **kw)
    await validate(session, fake({"reference": True, "empty": True, "repeat": True}))
    return project_id, version_id, builder


async def review(client, login, version_id, decision="approve", rubric=None, feedback="Clear task, tests check the fix."):
    return await client.post(f"/versions/{version_id}/reviews", headers=await login(RESEARCHER),
                             json={"decision": decision, "rubric": rubric or ALL_MET, "feedback": feedback})


async def test_a_builder_sees_why_a_check_failed_and_fixes_it_in_a_new_version(client, login, session):
    project_id, v1, builder = await submitted(client, login, fixture="passes_anything")
    await validate(session, fake({"reference": True, "empty": False, "repeat": True}))
    assert (await client.get(f"/versions/{v1}", headers=builder)).json()["status"] == "failed"
    why = [c for c in (await client.get(f"/versions/{v1}/checks", headers=builder)).json() if not c["passed"]]
    assert [c["check_name"] for c in why] == ["empty"] and why[0]["logs"]
    env_id = (await client.get(f"/versions/{v1}", headers=builder)).json()["environment_id"]
    v2 = (await client.post(f"/environments/{env_id}/versions", headers=builder, json=envfixtures.version_body("sound"))).json()
    assert v2["number"] == 2
    diff = (await client.get(f"/versions/{v2['id']}/diff", headers=builder)).json()
    assert diff["against_number"] == 1
    changed = [f for f in diff["files"] if f["change"] != "unchanged"]
    assert [(f["path"], f["change"]) for f in changed] == [("grader/test_slug.py", "changed")]
    assert any(line.startswith("+def test_empty_string") for line in changed[0]["diff"])
    assert (await client.get(f"/versions/{v2['id']}/diff", headers=await login(BRIGHTLINE_ADMIN))).status_code == 404


async def test_review_follows_the_briefs_rubric(client, login, session):
    project_id, version_id, builder = await in_review(client, login, session)
    queue = (await client.get(f"/versions?project_id={project_id}&status=in_review", headers=await login(RESEARCHER))).json()
    assert [v["id"] for v in queue] == [version_id] and queue[0]["vendor_name"] == "Kestrel Labs"
    partial = {BRIEF["review_rubric"][0]: True}
    assert (await review(client, login, version_id, rubric=partial)).status_code == 422
    one_unmet = {**ALL_MET, BRIEF["review_rubric"][1]: False}
    assert (await review(client, login, version_id, rubric=one_unmet)).status_code == 422  # approval needs every criterion
    assert (await client.post(f"/versions/{version_id}/reviews", headers=builder,
                              json={"decision": "approve", "rubric": ALL_MET, "feedback": "Looks good to me"})).status_code == 403
    sent_back = await review(client, login, version_id, decision="send_back", rubric=one_unmet,
                             feedback="The tests don't check whitespace handling, which the task statement promises.")
    assert sent_back.status_code == 201 and sent_back.json()["status"] == "failed"
    feedback = (await client.get(f"/versions/{version_id}/reviews", headers=builder)).json()
    assert feedback[0]["decision"] == "send_back" and "whitespace" in feedback[0]["feedback"]
    assert (await review(client, login, version_id)).status_code == 409  # only versions in review


async def test_progress_counts_environments_against_the_briefs_target(client, login, session):
    project_id, approved, _ = await in_review(client, login, session, name="one")
    await review(client, login, approved)
    await submitted(client, login, project_id=project_id, name="two", fixture="unsolvable")
    progress = (await client.get(f"/projects/{project_id}/environment-progress", headers=await login(KESTREL_ADMIN))).json()
    assert progress == {"target": 4, "environments": 2, "approved": 1, "in_review": 0, "failed": 0, "in_progress": 1}
    assert (await client.get(f"/projects/{project_id}/environment-progress", headers=await login(BRIGHTLINE_ADMIN))).status_code == 404


async def test_only_approved_versions_reach_the_catalog_without_their_reference_solution(client, login, session):
    project_id, version_id, _ = await in_review(client, login, session)
    assert (await client.get("/catalog", headers=TRAINING)).json() == []
    assert (await client.post(f"/catalog/{version_id}/pull", headers=TRAINING, json={})).status_code == 404
    await review(client, login, version_id)
    [entry] = (await client.get(f"/catalog?project_id={project_id}", headers=TRAINING)).json()
    assert entry["version_id"] == version_id and entry["test_command"]
    assert {f["part"] for f in entry["files"]} == {"task", "grader"}
    pulled = (await client.post(f"/catalog/{version_id}/pull", headers=TRAINING, json={"run_label": "run-7"})).json()
    paths = {f["path"] for f in pulled["files"]}
    assert paths == {"task/README.md", "task/slug.py", "grader/test_slug.py"}
    task = next(f for f in pulled["files"] if f["path"] == "task/slug.py")
    assert b"words[0]" in base64.b64decode(task["content_b64"])  # the unfixed task, not the reference
    pulls = (await client.get(f"/versions/{version_id}/pulls", headers=await login(DATA_OPS))).json()
    assert [(p["puller"], p["run_label"]) for p in pulls] == [("training", "run-7")]


async def test_only_training_can_use_the_catalog_and_quarantined_versions_leave_it(client, login, session):
    _, version_id, _ = await in_review(client, login, session)
    await review(client, login, version_id)
    for headers in ({}, await login(DATA_OPS), await login(KESTREL_ADMIN)):
        assert (await client.get("/catalog", headers=headers)).status_code == 401
    assert (await client.get(f"/versions/{version_id}/pulls", headers=await login(KESTREL_ADMIN))).status_code == 403
    await session.execute(text("UPDATE environment_versions SET status = 'quarantined' WHERE id = :v"), {"v": version_id})
    assert (await client.get("/catalog", headers=TRAINING)).json() == []
    assert (await client.post(f"/catalog/{version_id}/pull", headers=TRAINING, json={})).status_code == 404
