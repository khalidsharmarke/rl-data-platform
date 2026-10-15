"""M17 done-check, platform side: episode intake (TO1, D5, D6, D36, N4, N5).

Only training can send episodes; a resent batch changes nothing; every stored episode has
its run, step, version and vendor; transcripts are stored by hash; a batch naming an unknown
version is refused whole.
"""

import uuid

from sqlalchemy import text

from tests.people import DATA_OPS, KESTREL_ADMIN
from tests.test_review_catalog import TRAINING, in_review, review


async def approved(client, login, session):
    _, version_id, _ = await in_review(client, login, session)
    await review(client, login, version_id)
    return version_id


def batch(version_id, run="rl-run-001", ids=range(5), step=0):
    return {"run_label": run, "episodes": [
        {"episode_id": f"{run}-{i}", "version_id": version_id, "step": step + i, "reward": 1.0 if i % 2 else 0.0,
         "passed": bool(i % 2), "length": 30 + i, "touched": ["task/slug.py"], "transcript": f"attempt {i}"}
        for i in ids]}


async def test_only_training_can_send_episodes(client, login, session):
    version_id = await approved(client, login, session)
    for headers in ({}, await login(DATA_OPS), await login(KESTREL_ADMIN)):
        assert (await client.post("/ingest/episodes", headers=headers, json=batch(version_id))).status_code == 401


async def test_a_resent_batch_changes_nothing(client, login, session):
    version_id = await approved(client, login, session)
    first = (await client.post("/ingest/episodes", headers=TRAINING, json=batch(version_id))).json()
    assert first == {"run_label": "rl-run-001", "stored": 5, "duplicates": 0}
    again = (await client.post("/ingest/episodes", headers=TRAINING, json=batch(version_id))).json()
    assert again == {"run_label": "rl-run-001", "stored": 0, "duplicates": 5}
    overlap = (await client.post("/ingest/episodes", headers=TRAINING, json=batch(version_id, ids=range(3, 8)))).json()
    assert (overlap["stored"], overlap["duplicates"]) == (3, 2)
    assert await session.scalar(text("SELECT count(*) FROM episodes")) == 8
    assert await session.scalar(text("SELECT count(*) FROM training_runs WHERE label = 'rl-run-001'")) == 1


async def test_every_episode_keeps_its_run_step_version_and_vendor(client, login, session):
    version_id = await approved(client, login, session)
    await client.post("/ingest/episodes", headers=TRAINING, json=batch(version_id, run="rl-run-007", step=1400))
    rows = (await session.execute(text(
        "SELECT r.label, e.step, e.version_id, o.name, e.touched, e.transcript_hash FROM episodes e "
        "JOIN training_runs r ON r.id = e.run_id JOIN orgs o ON o.id = e.vendor_id ORDER BY e.seq"))).all()
    assert [r.step for r in rows] == [1400, 1401, 1402, 1403, 1404]  # stored in the order sent
    assert {(r.label, str(r.version_id), r.name) for r in rows} == {("rl-run-007", version_id, "Kestrel Labs")}
    assert rows[0].touched == ["task/slug.py"]
    assert await session.scalar(text("SELECT size FROM content_objects WHERE hash = :h"), {"h": rows[0].transcript_hash}) == len("attempt 0")


async def test_a_batch_naming_an_unknown_version_is_refused_whole(client, login, session):
    version_id = await approved(client, login, session)
    body = batch(version_id)
    body["episodes"][2]["version_id"] = str(uuid.uuid4())
    r = await client.post("/ingest/episodes", headers=TRAINING, json=body)
    assert r.status_code == 422 and "Unknown environment version" in r.text
    assert await session.scalar(text("SELECT count(*) FROM episodes")) == 0


async def test_episodes_never_change_once_stored(client, login, session):
    version_id = await approved(client, login, session)
    await client.post("/ingest/episodes", headers=TRAINING, json=batch(version_id))
    from tests.test_environments import database_refuses
    assert await database_refuses(session, "UPDATE episodes SET reward = 1", {})
    assert await database_refuses(session, "DELETE FROM episodes", {})
