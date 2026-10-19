"""M33 done-check: task types beyond comparing two answers (DC1, D53).

Each type has its own answer schema, gold check and agreement measure; routing, release,
scorecards and exports work the same for every type.
"""

import json

from sqlalchemy import text

from tests.builders import allocate, report
from tests.people import DATA_OPS, NORTHWIND_WORKER, NORTHWIND_WORKER_2, RESEARCHER

PROMPTS = ["How do I reverse a list in Python?", "Why is the sky blue?", "How long should I boil an egg?", "What is a thread?"]


async def make(client, login, task_type, *, gold=None, overlap=2, **extra):
    researcher = await login(RESEARCHER)
    r = await client.post("/projects", headers=researcher, json={
        "name": f"{task_type} project", "task_type": task_type, "instructions": "Do the task.", "target_volume": 8,
        "overlap": overlap, "gold_rate": "0.25", "release_after_minutes": 0, "release_cap_minutes": 0, **extra})
    assert r.status_code == 201, r.text
    tasks = [{"payload": {"item": i, "prompt": p, **({"response": "Use reversed() or slicing."} if task_type == "rating" else {})},
              **({"gold_answer": gold} if gold and i == 0 else {})} for i, p in enumerate(PROMPTS)]
    added = await client.post(f"/projects/{r.json()['id']}/tasks", headers=researcher, json={"tasks": tasks})
    return r.json(), added


async def answer_all(client, headers, project_id, answer_for):
    while (r := await client.post("/assignments/next", headers=headers, json={"project_id": project_id})).status_code == 200:
        a = r.json()
        assert a["task"]["task_type"]
        s = await client.post(f"/assignments/{a['id']}/submit", headers=headers, json={"answer": answer_for(a["task"]["payload"])})
        assert s.status_code == 201, s.text


async def quality(client, login, project_id):
    return (await client.get(f"/projects/{project_id}/quality", headers=await login(DATA_OPS))).json()


async def test_a_rating_project_scores_gold_and_agreement_within_one_point(client, login):
    project, added = await make(client, login, "rating", gold={"score": 4})
    assert added.status_code == 201
    await allocate(client, login, project["id"], "Northwind Data", "1")
    await answer_all(client, await login(NORTHWIND_WORKER), project["id"], lambda p: {"score": 5})  # gold 4: within one
    await answer_all(client, await login(NORTHWIND_WORKER_2), project["id"], lambda p: {"score": 2 if p["item"] == 1 else 4})
    workers = {w["label"]: w for w in (await quality(client, login, project["id"]))["workers"]}
    assert all(w["gold_accuracy"] == 1.0 for w in workers.values())
    # Three regular tasks each: 5 against 4 agrees twice, 5 against 2 doesn't.
    assert sorted(round(w["agreement"], 2) for w in workers.values()) == [0.67, 0.67]
    bad = await client.post("/projects", headers=await login(RESEARCHER), json={
        "name": "No score", "task_type": "rating", "instructions": "x", "target_volume": 1, "overlap": 1, "gold_rate": "0",
        "answer_schema": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}})
    assert bad.status_code == 422 and "score" in bad.text


async def test_a_writing_project_has_no_gold_or_agreement_and_still_releases(client, login):
    project, refused = await make(client, login, "writing", gold={"text": "x"})
    assert refused.status_code == 422 and "spot checks" in refused.text
    project, added = await make(client, login, "writing", overlap=1)
    await allocate(client, login, project["id"], "Northwind Data", "1")
    await answer_all(client, await login(NORTHWIND_WORKER), project["id"], lambda p: {"text": f"An answer to: {p['prompt']}"})
    worker = (await quality(client, login, project["id"]))["workers"][0]
    assert worker["gold_accuracy"] is None and worker["agreement"] is None and worker["submissions"] == 4
    researcher = await login(RESEARCHER)
    item = (await client.get(f"/projects/{project['id']}/review-queue", headers=researcher)).json()[0]
    assert item["task_type"] == "writing" and item["answer"]["text"].startswith("An answer to")
    await report(client, project["id"], NORTHWIND_WORKER, rules=())
    dataset = (await client.post(f"/projects/{project['id']}/exports", headers=researcher)).json()
    rows = [json.loads(r) for r in (await client.get(f"/datasets/{dataset['id']}/download", headers=researcher)).text.splitlines()]
    assert len(rows) == 4 and all(r["answer"]["text"] for r in rows)


async def test_unknown_task_types_are_refused(client, login):
    r = await client.post("/projects", headers=await login(RESEARCHER), json={
        "name": "x", "task_type": "ranking", "instructions": "x", "target_volume": 1, "overlap": 1, "gold_rate": "0"})
    assert r.status_code == 422 and "pairwise_preference" in r.text
