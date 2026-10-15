"""M18 done-check: training statistics and dashboards (TO2, TO4, TO5, EC3, D6, D39).

Statistics match a hand-computed run and don't change when the processor runs again; new
episodes show after one processor pass, well inside 30 s of arriving; a transcript opens from
the episode view; only Anthropic sees runs, and a researcher can flag a version from them.
"""

from app import training_stats
from app.config import settings
from tests.people import DATA_OPS, KESTREL_ADMIN, RESEARCHER
from tests.test_ingest import approved
from tests.test_review_catalog import TRAINING


def episodes(version_id, run="rl-run-001", start=0, n=10, passed_every=1, length=40, step=0):
    """n episodes; every passed_every-th one passes."""
    return {"run_label": run, "episodes": [
        {"episode_id": f"{run}-{start + i}", "version_id": version_id, "step": step, "reward": 1.0 if i % passed_every == 0 else 0.0,
         "passed": i % passed_every == 0, "length": length, "touched": ["task/slug.py"], "transcript": f"episode {start + i}"}
        for i in range(n)]}


async def run_of(client, login, label="rl-run-001"):
    runs = (await client.get("/runs", headers=await login(RESEARCHER))).json()
    run = next(r for r in runs if r["label"] == label)
    return (await client.get(f"/runs/{run['id']}", headers=await login(RESEARCHER))).json()


async def test_statistics_match_a_hand_computed_run(client, login, session):
    version_id = await approved(client, login, session)
    # Bucket 0 (step 0): 10 episodes, 5 pass, length 40. Bucket 1 (step 500): 10 episodes, all pass, length 4.
    await client.post("/ingest/episodes", headers=TRAINING, json=episodes(version_id, passed_every=2))
    await client.post("/ingest/episodes", headers=TRAINING, json=episodes(version_id, start=10, step=500, length=4))
    assert await training_stats.process(session) == 20
    [v] = (await run_of(client, login))["versions"]
    assert v["episodes"] == 20 and v["solve_rate"] == 0.75 and v["mean_reward"] == 0.75
    assert abs(v["reward_variance"] - 0.1875) < 1e-9 and v["mean_length"] == 22
    assert v["difficulty"] == "easy" and v["vendor_name"] == "Kestrel Labs"
    assert [(p["step"], p["solve_rate"], p["mean_length"]) for p in v["series"]] == [(0, 0.5, 40), (500, 1.0, 4)]


async def test_processing_again_changes_nothing_and_new_episodes_add_on(client, login, session):
    version_id = await approved(client, login, session)
    await client.post("/ingest/episodes", headers=TRAINING, json=episodes(version_id, passed_every=2))
    await training_stats.process(session)
    assert await training_stats.process(session) == 0
    assert (await run_of(client, login))["versions"][0]["episodes"] == 10
    await client.post("/ingest/episodes", headers=TRAINING, json=episodes(version_id, start=10))  # all pass
    await training_stats.process(session)
    v = (await run_of(client, login))["versions"][0]
    assert (v["episodes"], v["solve_rate"]) == (20, 0.75)


async def test_a_jump_shows_after_one_processor_pass(client, login, session):
    version_id = await approved(client, login, session)
    await client.post("/ingest/episodes", headers=TRAINING, json=episodes(version_id, passed_every=5, length=40))
    await training_stats.process(session)
    await client.post("/ingest/episodes", headers=TRAINING, json=episodes(version_id, start=10, step=500, length=4))
    before = await run_of(client, login)
    assert before["unprocessed"] == 10 and len(before["versions"][0]["series"]) == 1
    await training_stats.process(session)
    after = await run_of(client, login)
    assert after["unprocessed"] == 0 and [p["solve_rate"] for p in after["versions"][0]["series"]] == [0.2, 1.0]
    # The processor runs every tick of the job worker: inside A9's 10-30 s behind training.
    assert settings.jobs_tick_seconds <= 30


async def test_a_transcript_opens_from_the_episode_view(client, login, session):
    version_id = await approved(client, login, session)
    await client.post("/ingest/episodes", headers=TRAINING, json=episodes(version_id, n=3))
    run = await run_of(client, login)
    listed = (await client.get(f"/runs/{run['run']['id']}/episodes?version_id={version_id}", headers=await login(RESEARCHER))).json()
    assert [e["episode_id"] for e in listed] == ["rl-run-001-2", "rl-run-001-1", "rl-run-001-0"]  # newest first
    detail = (await client.get("/episodes/rl-run-001-1", headers=await login(DATA_OPS))).json()
    assert detail["transcript"] == "episode 1" and detail["run_label"] == "rl-run-001" and detail["vendor_name"] == "Kestrel Labs"


async def test_only_anthropic_sees_runs_and_a_researcher_can_flag_from_them(client, login, session):
    version_id = await approved(client, login, session)
    await client.post("/ingest/episodes", headers=TRAINING, json=episodes(version_id, n=2))
    kestrel = await login(KESTREL_ADMIN)
    assert (await client.get("/runs", headers=kestrel)).status_code == 403
    assert (await client.get("/episodes/rl-run-001-0", headers=kestrel)).status_code == 403
    flagged = await client.post("/cases/environment", headers=await login(RESEARCHER),
                                json={"version_id": version_id, "observation": "Solve rate jumped to 100% with four-turn attempts."})
    assert flagged.status_code == 201 and flagged.json()["owner"] == "data_ops"
