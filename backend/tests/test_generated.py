"""M29 done-check: generated tasks reviewed by sample, families stopped, difficulty calibrated (EC3, EC7, D49).

Researchers review a sample of each batch; approving it approves the batch, a send-back sends
the batch back and stops its family until a researcher resumes it. Generated versions no sample
drew never reach the review queue. Each family shows intended against measured difficulty.
"""

import base64

from sqlalchemy import text

from app import clock, sandbox
from app.config import settings
from app.seed import seed
from tests.people import RESEARCHER
from tests.test_environments import BRIEF, allocate_brief, bundle, make_brief
from tests.test_validation import fake

GENERATOR = {"Authorization": "Bearer rlp1_test-generator-token"}
RUBRIC = {k: True for k in BRIEF["review_rubric"]}


async def batch(client, session, brief, name, family="clamp", n=5, failing=1) -> list[str]:
    """n generated versions in one batch; the last `failing` fail the checks. Returns version ids."""
    ids = []
    for i in range(n):
        env = (await client.post("/environments", headers=GENERATOR, json={"project_id": brief, "name": f"{family}-easy-{name}{i}"})).json()
        v = await client.post(f"/environments/{env['id']}/versions", headers=GENERATOR, json={
            **bundle(fix=f"return s.lower() or {i!r}[:0]"), "generated": {"family": family, "difficulty": ["easy", "medium", "hard"][i % 3], "batch": name}})
        assert v.status_code == 201, v.text
        await client.post(f"/versions/{v.json()['id']}/submit", headers=GENERATOR)
        ids.append(v.json()["id"])
    while (job := await sandbox.claim(session)) is not None:
        ok = job.payload["version_id"] not in ids[n - failing:]
        await sandbox.process(session, job, checks=fake({"reference": True, "empty": ok, "repeat": True}))
    return ids


async def setup(client, login, session, monkeypatch) -> str:
    monkeypatch.setattr(settings, "generator_token", "rlp1_test-generator-token")
    await seed(session)
    brief = await make_brief(client, login)
    assert (await allocate_brief(client, login, brief, vendor="Code task generator")).status_code == 201
    return brief


async def statuses(client, ids) -> list[str]:
    return [(await client.get(f"/versions/{i}", headers=GENERATOR)).json()["status"] for i in ids]


async def test_approving_the_sample_approves_the_batch(client, login, session, monkeypatch):
    brief = await setup(client, login, session, monkeypatch)
    ids = await batch(client, session, brief, "b1")
    ready = (await client.post(f"/projects/{brief}/generated-batches/b1/ready", headers=GENERATOR)).json()
    assert ready == {"batch": "b1", "versions": 5, "passed_checks": 4, "sampled": 3, "status": "in_review"}

    researcher = await login(RESEARCHER)
    queue = [v["id"] for v in (await client.get("/versions?status=in_review", headers=researcher)).json()]
    sampled = [i for i in ids if i in queue]
    assert len(sampled) == 3
    unsampled = next(i for i in ids[:4] if i not in sampled)
    r = await client.post(f"/versions/{unsampled}/reviews", headers=researcher, json={"decision": "approve", "rubric": RUBRIC, "feedback": "Fine"})
    assert r.status_code == 409
    for i in sampled:
        r = await client.post(f"/versions/{i}/reviews", headers=researcher, json={"decision": "approve", "rubric": RUBRIC, "feedback": "Fine"})
        assert r.status_code == 201, r.text
    assert await statuses(client, ids) == ["approved"] * 4 + ["failed"]
    catalog = (await client.get("/catalog", headers={"Authorization": "Bearer test-training-token"})).json()
    assert set(ids[:4]) <= {e["version_id"] for e in catalog}
    summary = (await client.get(f"/projects/{brief}/generated", headers=researcher)).json()
    assert summary["batches"][0]["status"] == "approved" and summary["batches"][0]["sample_reviewed"] == 3


async def test_a_send_back_stops_the_batch_and_its_family_until_resumed(client, login, session, monkeypatch):
    brief = await setup(client, login, session, monkeypatch)
    ids = await batch(client, session, brief, "b2", failing=0)
    await client.post(f"/projects/{brief}/generated-batches/b2/ready", headers=GENERATOR)
    researcher = await login(RESEARCHER)
    first = next(v["id"] for v in (await client.get("/versions?status=in_review", headers=researcher)).json() if v["id"] in ids)
    r = await client.post(f"/versions/{first}/reviews", headers=researcher, json={
        "decision": "send_back", "rubric": {k: False for k in RUBRIC}, "feedback": "The README contradicts the tests."})
    assert r.status_code == 201
    assert set(await statuses(client, ids)) == {"failed"}
    summary = (await client.get(f"/projects/{brief}/generated", headers=GENERATOR)).json()
    assert [s["family"] for s in summary["stopped"]] == ["clamp"] and summary["batches"][0]["status"] == "sent_back"
    assert (await client.post(f"/family-stops/{summary['stopped'][0]['id']}/resume", headers=researcher)).status_code == 200
    assert (await client.get(f"/projects/{brief}/generated", headers=GENERATOR)).json()["stopped"] == []


async def test_each_family_shows_intended_against_measured_difficulty(client, login, session, monkeypatch):
    brief = await setup(client, login, session, monkeypatch)
    ids = await batch(client, session, brief, "b3", n=3, failing=0)
    run = await session.scalar(text("INSERT INTO training_runs (label, first_seen_at) VALUES ('rl-test', :now) RETURNING id"), {"now": clock.now()})
    for version_id, (episodes, passed) in zip(ids, [(100, 97), (100, 50), (100, 90)]):
        await session.execute(text("INSERT INTO training_stats (run_id, version_id, step_bucket, episodes, passed, reward_sum, reward_sq_sum, length_sum) "
                                   "VALUES (:r, :v, 0, :e, :p, :f, :f, 3000)"), {"r": run, "v": version_id, "e": episodes, "p": passed, "f": float(passed)})
    families = (await client.get(f"/projects/{brief}/generated", headers=await login(RESEARCHER))).json()["families"]
    assert [(f["intended"], f["measured"]) for f in families] == [("easy", "too easy"), ("medium", "medium"), ("hard", "easy")]
