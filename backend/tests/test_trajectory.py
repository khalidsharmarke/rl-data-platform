"""M34 done-check: trajectory review, the posting's agentic QA (DC1, D54, TO5).

Training's episodes become tasks: numbered steps and what the grader said, with the episode's id
kept in the task. A reviewer names the first step that goes wrong and judges the outcome; gold
and agreement are scored on both.
"""

from tests.builders import allocate
from tests.people import DATA_OPS, NORTHWIND_WORKER, NORTHWIND_WORKER_2, RESEARCHER
from tests.test_ingest import approved
from tests.test_review_catalog import TRAINING

HACK = "read task/README.md\nedit task/slug.py: return \"\"\nrun tests: passed"
HONEST = "read task/README.md\nread task/slug.py\nrun tests: 1 failed\nedit task/slug.py: handle ''\nrun tests: passed"


async def project_with_episodes(client, login, session):
    version_id = await approved(client, login, session)
    episodes = [{"episode_id": f"rl-traj-{i}", "version_id": version_id, "step": i * 100, "reward": 1.0, "passed": True,
                 "length": 3 if i % 2 else 5, "touched": ["task/slug.py"], "transcript": HACK if i % 2 else HONEST} for i in range(6)]
    assert (await client.post("/ingest/episodes", headers=TRAINING, json={"run_label": "rl-traj", "episodes": episodes})).status_code == 200
    researcher = await login(RESEARCHER)
    project = (await client.post("/projects", headers=researcher, json={
        "name": "Agent transcripts", "task_type": "trajectory_review", "instructions": "Find where it goes wrong.",
        "target_volume": 12, "overlap": 2, "gold_rate": "0.3", "release_after_minutes": 0, "release_cap_minutes": 0})).json()
    gold = {"rl-traj-0": {"first_wrong_step": 0, "outcome": "succeeded"}, "rl-traj-1": {"first_wrong_step": 2, "outcome": "failed"}}
    r = await client.post(f"/projects/{project['id']}/tasks/from-episodes", headers=researcher, json={"episodes": [
        {"episode_id": e["episode_id"], **({"gold_answer": gold[e["episode_id"]]} if e["episode_id"] in gold else {})} for e in episodes]})
    assert r.status_code == 201, r.text
    assert r.json() == {"tasks": 6, "gold": 2}
    await allocate(client, login, project["id"], "Northwind Data", "1")
    return project


def judge(payload, careful=True):
    """A reviewer who spots the step that returns "", and one who trusts the grader."""
    hacked = any('return ""' in s for s in payload["steps"])
    if hacked and careful:
        return {"first_wrong_step": next(i for i, s in enumerate(payload["steps"], 1) if 'return ""' in s), "outcome": "failed"}
    return {"first_wrong_step": 0, "outcome": "succeeded"}


async def review_all(client, headers, project_id, careful):
    while (r := await client.post("/assignments/next", headers=headers, json={"project_id": project_id})).status_code == 200:
        a = r.json()
        assert a["task"]["task_type"] == "trajectory_review" and a["task"]["payload"]["steps"][0] == "read task/README.md"
        assert a["task"]["payload"]["graded"] == "passed" and a["task"]["payload"]["episode_id"].startswith("rl-traj-")
        s = await client.post(f"/assignments/{a['id']}/submit", headers=headers, json={"answer": judge(a["task"]["payload"], careful)})
        assert s.status_code == 201, s.text


async def test_reviewers_are_scored_on_the_first_wrong_step_and_the_outcome(client, login, session):
    project = await project_with_episodes(client, login, session)
    await review_all(client, await login(NORTHWIND_WORKER), project["id"], careful=True)
    await review_all(client, await login(NORTHWIND_WORKER_2), project["id"], careful=False)
    q = (await client.get(f"/projects/{project['id']}/quality", headers=await login(DATA_OPS))).json()
    by_accuracy = sorted(w["gold_accuracy"] for w in q["workers"])
    assert by_accuracy == [0.5, 1.0]  # the trusting reviewer misses the hacked gold episode
    # On the four regular tasks they agree only where nothing was hacked: one of them.
    assert {round(w["agreement"], 2) for w in q["workers"]} == {0.5}


async def test_only_trajectory_projects_take_episodes_and_gold_must_fit_the_transcript(client, login, session):
    project = await project_with_episodes(client, login, session)
    r = await client.post(f"/projects/{project['id']}/tasks/from-episodes", headers=await login(RESEARCHER), json={"episodes": [
        {"episode_id": "rl-traj-0", "gold_answer": {"first_wrong_step": 9, "outcome": "failed"}}]})
    assert r.status_code == 422 and "isn't in the transcript" in r.text
    r = await client.post(f"/projects/{project['id']}/tasks/from-episodes", headers=await login(RESEARCHER), json={"episodes": [
        {"episode_id": "no-such-episode"}]})
    assert r.status_code == 422
