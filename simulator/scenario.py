"""The scripted demo (docs/BUILD.md §1): three vendors on one project, one worker turns abusive.

Drives the platform only through its public API, like real clients. Runs against a fresh
database with the demo clock on, so a working day of simulated time fits into about a
minute. Worker behavior comes from profiles; one Brightline worker switches from average
work to pasting near-identical, AI-written answers part-way through.

Each simulated worker waits (in real time) for its simulated reading time divided by the
clock speed, so the platform's own time-on-task measurement sees realistic durations.
"""

import asyncio
import os
import random
import sys
from dataclasses import dataclass, field

import httpx

import demo_content

API = os.environ.get("API_URL", "http://api:8000")
SPEED = int(os.environ.get("DEMO_CLOCK_SPEED", "180"))
TASKS = int(os.environ.get("DEMO_TASKS", "3000"))
GOLD_RATE = 0.05
SWITCH_AT = 0.35  # share of expected answers after which the switcher starts pasting
SEED = 2026

PROFILES = {
    # accuracy on the better answer, simulated seconds per task, writes a reason, pastes it
    "careful": dict(accuracy=0.95, seconds=(30, 70), reason=0.6, paste=False),
    "average": dict(accuracy=0.86, seconds=(18, 40), reason=0.3, paste=False),
    "sloppy": dict(accuracy=0.62, seconds=(7, 15), reason=0.0, paste=False),
    "ai_paster": dict(accuracy=0.72, seconds=(2, 4), reason=1.0, paste=True),
}

ROSTERS = {
    "Northwind Data": ["careful"] * 7 + ["average"] * 3,
    "Kestrel Labs": ["careful"] * 3 + ["average"] * 6 + ["sloppy"],
    "Brightline Annotation": ["careful"] * 2 + ["average"] * 5 + ["sloppy"] * 2 + ["switcher"],
}
SHARES = {"Northwind Data": "0.40", "Kestrel Labs": "0.35", "Brightline Annotation": "0.25"}

FIRST = ["Amara", "Bao", "Carmen", "Dmitri", "Esi", "Farid", "Greta", "Hiro", "Ines", "Jamal", "Keiko", "Lars",
         "Maya", "Nikhil", "Olga", "Pedro", "Quinn", "Rosa", "Sven", "Tariq", "Uma", "Viktor", "Wen", "Yara", "Zane",
         "Aiko", "Bruno", "Chloe", "Diego", "Elif"]
LAST = ["Abara", "Brandt", "Castillo", "Dube", "Eriksen", "Fong", "Gallo", "Haddad", "Ivanova", "Jensen", "Kaur",
        "Lopes", "Moreau", "Nakamura", "Osei", "Park", "Quint", "Rossi", "Silva", "Tran"]
TYPED_REASONS = ["Directly answers the question.", "The other one is vague.", "Gives a usable example.",
                 "More accurate.", "Clearer steps.", "The other answer is wrong.", "Covers the edge case."]
PASTED_REASON = ("Answer {choice} is more helpful because it directly addresses the user's question with accurate, "
                 "actionable detail and a clear structure, whereas the other response is vague and less useful.")


@dataclass
class Progress:
    expected: int
    answered: int = 0
    by_vendor: dict = field(default_factory=dict)


async def wait_for_api(c: httpx.AsyncClient) -> None:
    for _ in range(120):
        try:
            if (await c.get("/health")).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1)
    sys.exit("API never became healthy")


async def as_person(c: httpx.AsyncClient, name: str) -> dict:
    users = (await c.get("/auth/demo-users")).json()
    user = next(u for u in users if u["display_name"] == name)
    token = (await c.post("/auth/demo-login", json={"user_id": user["id"]})).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def sim_sleep(sim_seconds: float):
    return asyncio.sleep(sim_seconds / SPEED)


async def setup(c: httpx.AsyncClient) -> tuple[str, dict, list[tuple[str, str, str]]]:
    users = (await c.get("/auth/demo-users")).json()
    by_role = lambda role: next(u["display_name"] for u in users if u["role"] == role)  # noqa: E731
    researcher, ops = await as_person(c, by_role("researcher")), await as_person(c, by_role("data_ops"))

    project = (await c.post("/projects", headers=researcher, json={
        "name": "Which answer is more helpful?", "task_type": demo_content.TASK_TYPE,
        "answer_schema": demo_content.ANSWER_SCHEMA, "instructions": demo_content.INSTRUCTIONS,
        "target_volume": TASKS * 2, "overlap": 2, "gold_rate": str(GOLD_RATE), "quality_bar": "0.8",
        "min_gold_results": 5})).json()
    generated = demo_content.generate(TASKS, seed=SEED)
    truth = {g.payload["item"]: g.better for g in generated}
    gold = demo_content.gold_items(TASKS, GOLD_RATE, seed=SEED)
    batch = [{"payload": g.payload, **({"gold_answer": {"choice": g.better}} if i in gold else {})}
             for i, g in enumerate(generated)]
    for start in range(0, len(batch), 1000):
        r = await c.post(f"/projects/{project['id']}/tasks", headers=researcher, json={"tasks": batch[start:start + 1000]})
        r.raise_for_status()

    orgs = {o["name"]: o["id"] for o in (await c.get("/orgs", headers=ops)).json()}
    workers = []
    rng = random.Random(SEED)
    names = iter(rng.sample([f"{f} {l}" for f in FIRST for l in LAST], 60))
    for vendor, share in SHARES.items():
        contract = (await c.get(f"/orgs/{orgs[vendor]}/contracts", headers=ops)).json()[0]
        r = await c.post(f"/projects/{project['id']}/allocations", headers=ops,
                         json={"vendor_id": orgs[vendor], "contract_id": contract["id"], "share": share})
        r.raise_for_status()
        admin = await as_person(c, next(u["display_name"] for u in users if u["role"] == "vendor_admin" and u["org_name"] == vendor))
        for profile in ROSTERS[vendor]:
            name = next(names)
            r = await c.post(f"/orgs/{orgs[vendor]}/members", headers=admin, json={"display_name": name, "role": "worker"})
            r.raise_for_status()
            workers.append((vendor, name, profile))
    return project["id"], truth, workers


async def run_worker(c, project_id, truth, vendor, name, profile, progress: Progress, rng: random.Random) -> None:
    headers = await as_person(c, name)
    switcher = profile == "switcher"
    behavior = "average" if switcher else profile
    until_break = rng.randint(15, 40)
    misses = 0
    while True:
        if switcher and behavior != "ai_paster" and progress.answered >= SWITCH_AT * progress.expected:
            behavior = "ai_paster"
            print(f"  {name} ({vendor}) starts pasting AI-written answers", flush=True)
        r = await c.post("/assignments/next", headers=headers, json={"project_id": project_id})
        if r.status_code == 204:
            misses += 1
            if misses > 3:
                return
            await sim_sleep(10 * 60)  # others' expired leases may free tasks up
            continue
        if r.status_code == 403:
            return  # suspended
        r.raise_for_status()
        misses = 0
        a = r.json()
        p = PROFILES[behavior]
        await sim_sleep(rng.uniform(*p["seconds"]))
        better = truth[a["task"]["payload"]["item"]]
        choice = better if rng.random() < p["accuracy"] else ("B" if better == "A" else "A")
        answer = {"choice": choice}
        if rng.random() < p["reason"]:
            answer["rationale"] = PASTED_REASON.format(choice=choice) if p["paste"] else rng.choice(TYPED_REASONS)
        s = await c.post(f"/assignments/{a['id']}/submit", headers=headers,
                         json={"answer": answer, "client_signals": {"pasted": p["paste"]}})
        if s.status_code == 403:
            return
        if s.status_code == 409:
            continue  # lease expired during a long pause
        s.raise_for_status()
        progress.answered += 1
        progress.by_vendor[vendor] = progress.by_vendor.get(vendor, 0) + 1
        until_break -= 1
        if until_break == 0:
            await sim_sleep(rng.uniform(5, 25) * 60)
            until_break = rng.randint(15, 40)


async def report(c, project_id) -> None:
    users = (await c.get("/auth/demo-users")).json()
    ops = await as_person(c, next(u["display_name"] for u in users if u["role"] == "data_ops"))
    q = (await c.get(f"/projects/{project_id}/quality", headers=ops)).json()
    pct = lambda x: "  —" if x is None else f"{x * 100:4.0f}%"  # noqa: E731
    print(f"\n{'Vendor':24} {'answers':>8} {'gold acc':>9} {'agreement':>10} {'accepted':>9}")
    for v in q["vendors"]:
        print(f"{v['label']:24} {v['submissions']:>8} {pct(v['gold_accuracy']):>9} {pct(v['agreement']):>10} {v['accepted']:>9}")


async def main() -> None:
    limits = httpx.Limits(max_connections=60)
    async with httpx.AsyncClient(base_url=API, timeout=60, limits=limits) as c:
        await wait_for_api(c)
        print(f"Setting up: {TASKS} tasks, demo clock at {SPEED}x", flush=True)
        project_id, truth, workers = await setup(c)
        gold = round(TASKS * GOLD_RATE)
        progress = Progress(expected=(TASKS - gold) * 2 + gold * 2 * len(SHARES))
        print(f"Running {len(workers)} simulated workers across {len(SHARES)} vendors", flush=True)

        async def ticker():
            while True:
                await asyncio.sleep(5)
                print(f"  {progress.answered}/{progress.expected} answers", flush=True)

        tick = asyncio.create_task(ticker())
        rngs = random.Random(SEED)
        await asyncio.gather(*(run_worker(c, project_id, truth, v, n, p, progress, random.Random(rngs.random()))
                               for v, n, p in workers))
        tick.cancel()
        print(f"Done: {progress.answered} answers", flush=True)
        await report(c, project_id)


if __name__ == "__main__":
    asyncio.run(main())
