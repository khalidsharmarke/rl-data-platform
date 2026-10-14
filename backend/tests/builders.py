"""Helpers that set up projects, contracts and allocations through the API."""

from app import demo_content
from tests.people import DATA_OPS, RESEARCHER

# How the detection service signs in (see the client fixture).
DETECTION = {"Authorization": "Bearer test-detection-token"}
SIGNALS = {
    "speed": "9 of the last 10 answers were submitted faster than the task can be read.",
    "paste": "Reason text was pasted rather than typed in 10 of the last 10 answers.",
    "duplicate": "The same reason, word for word, was given for 10 of the last 10 answers to different questions.",
}


async def org_ids(client, headers) -> dict[str, str]:
    return {o["name"]: o["id"] for o in (await client.get("/orgs", headers=headers)).json()}


async def make_project(client, login, *, tasks: int, gold_every: int | None = None, overlap: int = 1,
                       release_after: int = 0, release_cap: int = 0, min_gold_results: int = 5) -> tuple[str, list]:
    """A project; by default anything checked and accepted is released at once (no gap, no cap)."""
    researcher = await login(RESEARCHER)
    project = (
        await client.post(
            "/projects",
            headers=researcher,
            json={
                "name": f"Test project {tasks}/{gold_every}/{overlap}",
                "task_type": demo_content.TASK_TYPE,
                "answer_schema": demo_content.ANSWER_SCHEMA,
                "instructions": demo_content.INSTRUCTIONS,
                "target_volume": tasks * overlap,
                "overlap": overlap,
                "gold_rate": "0.05",
                "release_after_minutes": release_after,
                "release_cap_minutes": release_cap,
                "min_gold_results": min_gold_results,
            },
        )
    ).json()
    generated = demo_content.generate(tasks, seed=11)
    body = [
        {"payload": g.payload, **({"gold_answer": {"choice": g.better}} if gold_every and i % gold_every == 0 else {})}
        for i, g in enumerate(generated)
    ]
    response = await client.post(f"/projects/{project['id']}/tasks", headers=researcher, json={"tasks": body})
    assert response.status_code == 201, response.text
    return project["id"], generated


async def make_contract(client, login, vendor: str, price="0.75", unit="task") -> str:
    ops = await login(DATA_OPS)
    vendor_id = (await org_ids(client, ops))[vendor]
    response = await client.post(
        f"/orgs/{vendor_id}/contracts", headers=ops,
        json={"kind": "human_work", "price": price, "price_unit": unit, "effective_from": "2026-01-01"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def allocate(client, login, project_id: str, vendor: str, share: str, contract_id: str | None = None):
    ops = await login(DATA_OPS)
    vendor_id = (await org_ids(client, ops))[vendor]
    contract_id = contract_id or await make_contract(client, login, vendor)
    return await client.post(
        f"/projects/{project_id}/allocations", headers=ops,
        json={"vendor_id": vendor_id, "contract_id": contract_id, "share": share},
    )


async def answer_all(client, headers, project_id, generated, *, correct: bool, rationale: str | None = None) -> list[dict]:
    """Claim and answer every available task as one worker. Returns [{submission_id, task_id, item}]."""
    truth = {g.payload["item"]: g.better for g in generated}
    done = []
    while (r := await client.post("/assignments/next", headers=headers, json={"project_id": project_id})).status_code == 200:
        a = r.json()
        item = a["task"]["payload"]["item"]
        choice = truth[item] if correct else ("B" if truth[item] == "A" else "A")
        answer = {"choice": choice, **({"rationale": rationale} if rationale else {})}
        s = await client.post(f"/assignments/{a['id']}/submit", headers=headers, json={"answer": answer})
        assert s.status_code == 201, s.text
        done.append({"submission_id": s.json()["id"], "task_id": a["task"]["id"], "item": item})
    return done


async def answer_like(client, headers, project_id, *, n: int, pasted: bool, reason: str | None) -> list[str]:
    """Answer up to n tasks with a fixed behavior. Returns submission ids."""
    ids = []
    for _ in range(n):
        r = await client.post("/assignments/next", headers=headers, json={"project_id": project_id})
        if r.status_code != 200:
            break
        a = r.json()
        answer = {"choice": "A", **({"rationale": reason} if reason else {})}
        s = await client.post(f"/assignments/{a['id']}/submit", headers=headers, json={"answer": answer, "client_signals": {"pasted": pasted}})
        ids.append(s.json()["id"])
    return ids


async def user_id(client, name: str) -> str:
    return next(u["id"] for u in (await client.get("/auth/demo-users")).json() if u["display_name"] == name)


async def report(client, project_id: str, worker: str, *, severity: str = "case", rules=("speed", "paste", "duplicate"),
                 submission_ids=(), processed_through: int | None = None):
    """Report findings the way the detection service does."""
    events = (await client.get("/detection/events", headers=DETECTION, params={"limit": 2000})).json()["events"]
    through = processed_through if processed_through is not None else (events[-1]["id"] if events else 0)
    worker_id = await user_id(client, worker)
    body = {"detector": "rules-v1", "processed_through": through, "findings": [
        {"worker_id": worker_id, "project_id": project_id, "rule": r, "rule_version": f"{r}-v1", "severity": severity,
         "observation": SIGNALS[r], "evidence": {"submission_ids": list(submission_ids)}, "through_event": through}
        for r in rules]}
    r = await client.post("/detection/findings", headers=DETECTION, json=body)
    assert r.status_code == 202, r.text
    return r.json()
