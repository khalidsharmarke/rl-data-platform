"""The scripted demo (docs/BUILD.md §1): three vendors on one project, one worker turns abusive.

Drives the platform only through its public API, like real clients. Runs against a fresh
database with the demo clock on, so a working day of simulated time fits into about a
minute. Worker behavior comes from profiles; one Brightline worker switches from average
work to pasting near-identical, AI-written answers part-way through.

Each simulated worker waits (in real time) for its simulated reading time divided by the
clock speed, so the platform's own time-on-task measurement sees realistic durations.
"""

import asyncio
import base64
import json
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

import demo_content
import oci
import rlp

API = os.environ.get("API_URL", "http://api:8000")
SPEED = int(os.environ.get("DEMO_CLOCK_SPEED", "180"))
TASKS = int(os.environ.get("DEMO_TASKS", "3000"))
GOLD_RATE = 0.05
SWITCH_AT = 0.5  # share of its vendor's expected answers after which the switcher starts pasting
SEED = 2026
# Autopilot plays the people's decisions in the story: the vendor admin suspends the flagged
# worker after reviewing the case, and data ops cleans up. Turn it off to make those decisions
# by hand (for a recorded walkthrough). The researcher's routine exports always run.
AUTOPILOT = os.environ.get("DEMO_AUTOPILOT", "true") == "true"
REVIEW_DELAY_MINUTES = 30  # simulated time between a case opening and the admin deciding
CLEANUP_DELAY_MINUTES = 15  # simulated time between the decision and data ops' clean-up
EXPORT_EVERY_MINUTES = 30  # the researcher pulls released data every half hour (simulated)
SPOT_CHECK_EVERY_MINUTES = 10  # the researcher's spot checks: a batch this often (simulated)
SPOT_CHECK_BATCH = 12
# Release stage (PD7): an accepted answer waits at least this long, and at most this long
# before it's released as provisional (simulated minutes).
RELEASE_AFTER_MINUTES = 30
RELEASE_CAP_MINUTES = 120

PROFILES = {
    # accuracy on the better answer, simulated seconds per task, writes a reason, pastes it,
    # and how often a broken task is flagged rather than answered (DC5)
    "careful": dict(accuracy=0.95, seconds=(30, 70), reason=0.6, paste=False, flags=0.9),
    "average": dict(accuracy=0.86, seconds=(18, 40), reason=0.3, paste=False, flags=0.5),
    "sloppy": dict(accuracy=0.62, seconds=(7, 15), reason=0.0, paste=False, flags=0.0),
    # Pastes an answer without reading the task, so it is right about half the time.
    "ai_paster": dict(accuracy=0.5, seconds=(2, 4), reason=1.0, paste=True, flags=0.0),
    # Careful, but pastes each reason from their own notes: one signal on its own, so the
    # platform watches them more closely (more gold) and later gold clears them (AB14).
    "note_paster": dict(accuracy=0.95, seconds=(30, 70), reason=1.0, paste=True, notes=True, flags=0.9),
}
# The last batch of tasks has a bug: both answers are the same text (DC5). Added after the rest,
# so workers reach it near the end of the project, where it shows as a drop in agreement.
BROKEN_TASKS = 80
BROKEN_REASON = "Both answers are word for word the same, so neither can be more helpful."

ROSTERS = {
    "Northwind Data": ["careful"] * 6 + ["note_paster"] + ["average"] * 3,
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


# The second act: environments (docs/BUILD.md, environment milestones). A researcher's brief, given to
# Kestrel, whose builder submits four environments from the repo's fixtures; three are broken in the
# way one sandbox check is built to catch.
ENVIRONMENTS = Path(os.environ.get("ENVIRONMENTS_DIR", "/sim/environments"))
BUILDER = "Sofia Lindgren"
BRIEF = {
    "description": "Bug-fix tasks in small Python libraries, each with hidden tests that check the fix.",
    "languages": ["python"],
    "constraints": ["Nothing copied from public benchmarks", "Tests run in under 10 seconds"],
    "difficulty_mix": {"easy": 0.5, "medium": 0.5},
    "review_rubric": ["The task statement is clear and self-contained", "The tests check the behavior the task asks for",
                      "The task teaches a real debugging skill"],
}
SUBMISSIONS = [  # (environment name, fixture)
    ("slugify handles empty strings", "sound"),
    ("slugify exists", "passes_anything"),
    ("slugify with hyphens and underscores", "unsolvable"),
    ("slugify with a lucky test", "flaky"),
]
# The third act: training observability (docs/BUILD.md, training observability milestones). Kestrel's
# builder submits two more: a sound one, and one whose grader checks only the empty string. Both pass
# the sandbox and review; only training finds the hole.
TRAINING_SUBMISSIONS = [
    ("slugify drops punctuation", "sound_punctuation"),
    ("slugify edge cases", "weak_grader"),
]
WEAK = TRAINING_SUBMISSIONS[1][0]
REVIEW_DELAY_MINUTES_ENV = 20  # simulated time before the researcher reviews
QUARANTINE_AFTER_MINUTES = 40  # simulated time after training's pull before a researcher spots the copied task
BENCHMARK = "A researcher noticed this task copies a public benchmark problem word for word, against the brief's constraints."


def version_body(fixture: str) -> dict:
    base = ENVIRONMENTS / fixture
    manifest = json.loads((base / "manifest.json").read_text())
    files = [p for p in sorted(base.rglob("*")) if p.is_file() and p.name != "manifest.json"]
    return {**manifest, "files": [{"path": str(p.relative_to(base)), "part": p.relative_to(base).parts[0],
                                   "content_b64": base64.b64encode(p.read_bytes()).decode()} for p in files]}


async def people(c: httpx.AsyncClient) -> tuple[dict, dict, dict]:
    users = (await c.get("/auth/demo-users")).json()
    by_role = lambda role: next(u["display_name"] for u in users if u["role"] == role)  # noqa: E731
    return await as_person(c, by_role("researcher")), await as_person(c, by_role("data_ops")), await as_person(c, BUILDER)


async def submit_and_check(c: httpx.AsyncClient, builder: dict, brief_id: str, submissions: list[tuple[str, str]]) -> dict:
    """The builder submits each fixture as an environment; waits for the sandbox's verdict on each."""
    versions = {}
    for name, fixture in submissions:
        env = (await c.post("/environments", headers=builder, json={"project_id": brief_id, "name": name})).json()
        v = (await c.post(f"/environments/{env['id']}/versions", headers=builder, json=version_body(fixture))).json()
        (await c.post(f"/versions/{v['id']}/submit", headers=builder)).raise_for_status()
        versions[name] = v["id"]
    print(f"  Kestrel's builder submits {len(versions)} environments", flush=True)
    outcome = {}
    while len(outcome) < len(versions):
        for name, vid in versions.items():
            status = (await c.get(f"/versions/{vid}", headers=builder)).json()["status"]
            if name not in outcome and status in ("failed", "in_review"):
                failed = [ch["detail"] for ch in (await c.get(f"/versions/{vid}/checks", headers=builder)).json() if not ch["passed"]]
                outcome[name] = status
                print(f"  Sandbox: {name!r} {'failed: ' + failed[0] if failed else 'passed all its checks'}", flush=True)
        await asyncio.sleep(1)
    return versions


async def environment_act(c: httpx.AsyncClient) -> str:
    researcher, ops, builder = await people(c)
    brief = (await c.post("/projects", headers=researcher, json={
        "name": "Python bug fixes", "kind": "environment", "target_volume": 6, "brief": BRIEF})).json()
    kestrel = next(o for o in (await c.get("/orgs", headers=ops)).json() if o["name"] == "Kestrel Labs")
    contract = next(k for k in (await c.get(f"/orgs/{kestrel['id']}/contracts", headers=ops)).json() if k["kind"] == "environments")
    (await c.post(f"/projects/{brief['id']}/allocations", headers=ops,
                  json={"vendor_id": kestrel["id"], "contract_id": contract["id"], "share": "1"})).raise_for_status()
    print("  Researcher writes an environment brief; data ops gives it to Kestrel", flush=True)
    versions = await submit_and_check(c, builder, brief["id"], SUBMISSIONS)
    sound = versions[SUBMISSIONS[0][0]]
    if not AUTOPILOT:
        return brief["id"]  # the researcher's review and data ops' quarantine are made by hand
    await sim_sleep(REVIEW_DELAY_MINUTES_ENV * 60)
    (await c.post(f"/versions/{sound}/reviews", headers=researcher, json={
        "decision": "approve", "rubric": {k: True for k in BRIEF["review_rubric"]},
        "feedback": "Clear task; the tests check exactly the empty-string fix."})).raise_for_status()
    print(f"  Researcher approves {SUBMISSIONS[0][0]!r} against the brief's rubric", flush=True)
    while not (await c.get(f"/versions/{sound}/pulls", headers=ops)).json():
        await asyncio.sleep(1)
    print("  Training pulls it from the catalog", flush=True)
    await sim_sleep(QUARANTINE_AFTER_MINUTES * 60)
    case = (await c.post("/cases/environment", headers=ops, json={"version_id": sound, "observation": BENCHMARK})).json()
    print("  Data ops opens a case: the version is quarantined and leaves the catalog", flush=True)
    await sim_sleep(REVIEW_DELAY_MINUTES_ENV * 60)
    await c.post(f"/cases/{case['id']}/decision", headers=ops, json={"decision": "remove", "reason": "Confirmed against the benchmark: retire it."})
    print("  Data ops retires the version; the pull stays on record for lineage", flush=True)
    return brief["id"]


async def until(check, what: str, seconds: float = 300):
    """Polls check() until it returns something truthy; gives up with a message after `seconds` real seconds."""
    for _ in range(int(seconds)):
        if result := await check():
            return result
        await asyncio.sleep(1)
    raise RuntimeError(f"Gave up waiting for {what}")


async def training_act(c: httpx.AsyncClient, brief_id: str) -> None:
    researcher, ops, builder = await people(c)
    versions = await submit_and_check(c, builder, brief_id, TRAINING_SUBMISSIONS)
    if not AUTOPILOT:
        return  # the researcher's review and data ops' decision are made by hand
    await sim_sleep(REVIEW_DELAY_MINUTES_ENV * 60)
    for name, vid in versions.items():
        (await c.post(f"/versions/{vid}/reviews", headers=researcher, json={
            "decision": "approve", "rubric": {k: True for k in BRIEF["review_rubric"]},
            "feedback": "Clear task, and the tests check the fix the README asks for."})).raise_for_status()
    print(f"  Researcher approves both: nothing in the review shows that {WEAK!r} only tests the empty string", flush=True)
    weak = versions[WEAK]

    async def pulled():
        return (await c.get(f"/versions/{weak}/pulls", headers=ops)).json()

    run = (await until(pulled, "training to pull the new environments"))[0]["run_label"]
    print(f"  Training pulls both and starts {run}", flush=True)

    async def quarantined():
        return (await c.get(f"/versions/{weak}", headers=builder)).json()["status"] == "quarantined"

    await until(quarantined, "the hack detector to flag the weak grader")
    case = next(x for x in (await c.get("/cases", headers=ops)).json()
                if x["subject_type"] == "environment_version" and x["subject_handle"] == f"{WEAK} v1" and x["status"] == "open")
    print(f"  Hack detector, mid-run: {case['observations'][0]}", flush=True)
    print(f"  Its case quarantines {WEAK!r} without a person; {TRAINING_SUBMISSIONS[0][0]!r} keeps training", flush=True)

    async def reported():
        recall = (await c.get(f"/cases/{case['id']}/recall", headers=ops)).json()
        return recall if recall and any(r["run_label"] == run for r in recall["reports"]) else None

    recall = await until(reported, "training to report on the recall")
    mine = next(r for r in recall["runs"] if r["run_label"] == run)
    print(f"  Recall to training: {run} used it from step {mine['first_step']:,}, flagged attempts from step "
          f"{mine['first_flagged_step']:,}", flush=True)
    report = next(r for r in recall["reports"] if r["run_label"] == run)
    print(f"  Training reports: {run} rolled back to the checkpoint at step {report['checkpoint_step']:,}", flush=True)
    await sim_sleep(REVIEW_DELAY_MINUTES_ENV * 60)
    await c.post(f"/cases/{case['id']}/decision", headers=ops, json={
        "decision": "remove", "reason": "The flagged transcripts return an empty string and score full marks: the tests only check that case."})
    print("  Data ops retires the version; Kestrel's scorecard shows what happened in training", flush=True)


async def environments_and_training(c: httpx.AsyncClient) -> None:
    await training_act(c, await environment_act(c))


HARBOR = "Harbor Labs"
HARBOR_ADMIN, HARBOR_WORKER = "Ines Duarte", "Kai Mbeki"
HARBOR_STARTS_AFTER_MINUTES = 30
HARBOR_KIT = {"usr/local/lib/python3.12/site-packages/harbor_kit/__init__.py": b'"""Harbor\'s test helpers."""\n'}
SAMPLE_ANSWERS = {g.payload["item"]: g.better for g in demo_content.generate(5, seed=101)}  # the onboarding sample set


async def harbor_act(c: httpx.AsyncClient) -> None:
    """A fourth vendor onboards itself (VM3, D46): invite, token, image, a failed sample and a fixed one, worker samples."""
    await sim_sleep(HARBOR_STARTS_AFTER_MINUTES * 60)
    _researcher, ops, _builder = await people(c)
    org = (await c.post("/orgs", headers=ops, json={"name": HARBOR, "capabilities": ["human_work", "environments"]})).json()
    invite = (await c.post(f"/orgs/{org['id']}/invites", headers=ops, json={"display_name": HARBOR_ADMIN})).json()
    print(f"  Data ops creates {HARBOR} and sends its admin a single-use invite link", flush=True)
    await sim_sleep(10 * 60)
    admin = {"Authorization": "Bearer " + (await c.post("/auth/invites/accept", json={"code": invite["code"]})).json()["access_token"]}
    token = (await c.post("/tokens", headers=admin, json={"name": "harbor-ci", "scopes": ["environments", "read", "registry"]})).json()
    print(f"  {HARBOR_ADMIN} opens it, sees an empty checklist, and creates a token for Harbor's CI", flush=True)
    await asyncio.gather(harbor_ci(c, token["secret"]), harbor_samples(c, org["id"], admin))

    api = rlp.Api(API, token["secret"])
    state = await until(lambda: asyncio.to_thread(lambda: (s := rlp.checklist(api))["complete"] and s), "Harbor's checklist", 600)
    took = lambda at: (datetime.fromisoformat(at) - datetime.fromisoformat(state["invited_at"])).total_seconds() / 60  # noqa: E731
    print(f"  Harbor's CI sees the checklist complete: {took(state['first_accepted_at']):.0f} simulated minutes from invite to "
          f"first accepted work, {took(state['onboarded_at']):.0f} to ready", flush=True)
    if AUTOPILOT:
        brief = next(p for p in (await c.get("/projects", headers=ops)).json() if p["name"] == "Python bug fixes")
        contract = (await c.post(f"/orgs/{org['id']}/contracts", headers=ops, json={
            "kind": "environments", "price": "110", "price_unit": "environment", "effective_from": "2026-01-01"})).json()
        (await c.post(f"/projects/{brief['id']}/allocations", headers=ops,
                      json={"vendor_id": org["id"], "contract_id": contract["id"], "share": "1"})).raise_for_status()
        print(f"  Data ops allocates {HARBOR} real work: the Python bug-fix brief", flush=True)


async def harbor_ci(c: httpx.AsyncClient, secret: str) -> None:
    """Harbor's CI, with nothing but its token: push a runtime image, submit a sample, fix it when a check fails."""
    def push() -> str:
        def token_for(scope: str) -> str:
            r = httpx.get(f"{API}/registry/token", params={"service": "rl-registry", "scope": scope}, auth=("harbor-ci", secret))
            r.raise_for_status()
            return r.json()["token"]
        registry = oci.Registry(os.environ.get("REGISTRY_URL", "http://registry:5000"), token_for)
        repo = "vendors/harbor-labs/runtime"
        return f"{repo}@{oci.extend(registry, 'platform/base', '1', HARBOR_KIT, repo, '1')}"

    image = await asyncio.to_thread(push)
    print(f"  Harbor's CI logs in to the registry with the token and pushes {image.split('@')[0]}:1", flush=True)
    api = rlp.Api(API, secret)
    version, checks = await asyncio.to_thread(rlp.submit, api, ENVIRONMENTS / "passes_anything", image=image)
    failed = next(ch["detail"] for ch in checks if not ch["passed"])
    print(f"  Harbor's first sample fails a check: {failed}", flush=True)
    await sim_sleep(20 * 60)
    version, _ = await asyncio.to_thread(rlp.submit, api, ENVIRONMENTS / "sound", image=image)
    print(f"  Harbor's CI pushes a fix: version {version['number']} passes the sandbox's checks", flush=True)


async def harbor_samples(c: httpx.AsyncClient, org_id: str, admin: dict) -> None:
    await sim_sleep(15 * 60)
    (await c.post(f"/orgs/{org_id}/members", headers=admin, json={"display_name": HARBOR_WORKER, "role": "worker"})).raise_for_status()
    worker = await as_person(c, HARBOR_WORKER)
    project = next(p for p in (await c.get("/projects", headers=admin)).json() if p["onboarding"] and p["kind"] == "human_data")
    while (r := await c.post("/assignments/next", headers=worker, json={"project_id": project["id"]})).status_code == 200:
        a = r.json()
        await sim_sleep(90)
        choice = SAMPLE_ANSWERS[a["task"]["payload"]["item"]]
        (await c.post(f"/assignments/{a['id']}/submit", headers=worker, json={"answer": {"choice": choice}})).raise_for_status()
    print(f"  Harbor's admin adds {HARBOR_WORKER}, who answers the {len(SAMPLE_ANSWERS)} sample tasks", flush=True)


GENERATED_BRIEF = {
    "description": "Bug-fix tasks in small Python functions, generated from task families, each with hidden tests and probes.",
    "languages": ["python"], "constraints": ["Every task carries probes for the shortcuts its family invites"],
    "difficulty_mix": {"easy": 0.34, "medium": 0.33, "hard": 0.33},
    "review_rubric": ["The task statement matches what the tests check", "The tests would catch a wrong fix"],
}
GENERATED_REVIEW_MINUTES = 20  # simulated time before the researcher reviews a batch's sample


async def generation_act(c: httpx.AsyncClient) -> None:
    """Generated tasks (EC7, D48, D49): the generator fills a brief; a researcher reviews a sample; training calibrates."""
    researcher, ops, _builder = await people(c)
    brief = (await c.post("/projects", headers=researcher, json={
        "name": "Generated Python bug fixes", "kind": "environment", "target_volume": 9, "brief": GENERATED_BRIEF})).json()
    generator = next(o for o in (await c.get("/orgs", headers=ops)).json() if o["name"] == "Code task generator")
    contract = (await c.get(f"/orgs/{generator['id']}/contracts", headers=ops)).json()[0]
    (await c.post(f"/projects/{brief['id']}/allocations", headers=ops,
                  json={"vendor_id": generator["id"], "contract_id": contract["id"], "share": "1"})).raise_for_status()
    print("  Researcher writes a brief for generated tasks; data ops gives it to the code task generator", flush=True)

    async def batch_in_review():
        s = (await c.get(f"/projects/{brief['id']}/generated", headers=researcher)).json()
        return s if s["batches"] else None

    summary = await until(batch_in_review, "the generator's first batch", 900)
    b = summary["batches"][0]
    print(f"  The generator submits {b['batch']}: {b['versions']} tasks from {len({f['family'] for f in summary['families']})} families, "
          f"each checked like a vendor's; {b['sampled']} drawn for review", flush=True)
    for stop in await stopped_by_probes(c, brief["id"], researcher):
        print(f"  Probes catch a template bug: {stop}", flush=True)
    if not AUTOPILOT:
        return  # the researcher reviews the sample by hand
    await sim_sleep(GENERATED_REVIEW_MINUTES * 60)
    sample = [v for v in (await c.get("/versions?status=in_review", headers=researcher)).json() if v["batch"] == b["batch"]]
    for v in sample:
        (await c.post(f"/versions/{v['id']}/reviews", headers=researcher, json={
            "decision": "approve", "rubric": {k: True for k in GENERATED_BRIEF["review_rubric"]},
            "feedback": "Statement and tests agree; the probes cover the obvious shortcuts."})).raise_for_status()
    print(f"  Researcher approves the {len(sample)} sampled tasks, which approves the batch: it goes to the catalog", flush=True)

    async def calibrated():
        s = (await c.get(f"/projects/{brief['id']}/generated", headers=researcher)).json()
        rows = [f for f in s["families"] if f["approved"]]
        return rows if rows and all(f["episodes"] >= 60 for f in rows) else None

    for f in await until(calibrated, "training to use the generated tasks", 900):
        print(f"  Calibration, {f['family']} ({f['intended']}): {round(f['solve_rate'] * 100)}% solved in training, measured {f['measured']}", flush=True)


async def stopped_by_probes(c, brief_id: str, researcher: dict) -> list[str]:
    """Families whose versions the probes check failed."""
    out = []
    for v in (await c.get(f"/versions?project_id={brief_id}&status=failed", headers=researcher)).json():
        detail = next((ch["detail"] for ch in (await c.get(f"/versions/{v['id']}/checks", headers=researcher)).json()
                       if ch["check_name"] == "probes" and not ch["passed"]), None)
        if detail and not any(v["environment_name"].split("-")[0] in o for o in out):
            out.append(f"{v['environment_name'].split('-')[0]}: {detail} The generator stops the family.")
    return out


@dataclass
class Progress:
    expected: int
    expected_by_vendor: dict = field(default_factory=dict)
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
        "min_gold_results": 5, "release_after_minutes": RELEASE_AFTER_MINUTES,
        "release_cap_minutes": RELEASE_CAP_MINUTES})).json()
    generated = demo_content.generate(TASKS, seed=SEED)
    truth = {g.payload["item"]: g.better for g in generated}
    gold = demo_content.gold_items(TASKS, GOLD_RATE, seed=SEED)
    batch = [{"payload": g.payload, **({"gold_answer": {"choice": g.better}} if i in gold else {})}
             for i, g in enumerate(generated)]
    for start in range(0, len(batch), 1000):
        r = await c.post(f"/projects/{project['id']}/tasks", headers=researcher, json={"tasks": batch[start:start + 1000]})
        r.raise_for_status()
    broken = []
    for i, g in enumerate(demo_content.generate(BROKEN_TASKS, seed=SEED + 1)):
        item = TASKS + i
        broken.append({"payload": {**g.payload, "item": item, "response_b": g.payload["response_a"]}})
        truth[item] = g.better
    (await c.post(f"/projects/{project['id']}/tasks", headers=researcher, json={"tasks": broken})).raise_for_status()

    orgs = {o["name"]: o["id"] for o in (await c.get("/orgs", headers=ops)).json()}
    workers = []
    rng = random.Random(SEED)
    names = iter(rng.sample([f"{f} {l}" for f in FIRST for l in LAST], 60))
    for vendor, share in SHARES.items():
        contract = next(k for k in (await c.get(f"/orgs/{orgs[vendor]}/contracts", headers=ops)).json() if k["kind"] == "human_work")
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
    # Breaks follow time worked, not answers given, so a fast worker really is fast.
    until_break = rng.uniform(20, 40) * 60
    misses = 0
    while True:
        own = progress.by_vendor.get(vendor, 0)
        if switcher and behavior != "ai_paster" and own >= SWITCH_AT * progress.expected_by_vendor[vendor]:
            behavior = "ai_paster"
            print(f"  {name} ({vendor}) starts pasting AI-written answers", flush=True)
        r = await c.post("/assignments/next", headers=headers, json={"project_id": project_id})
        if r.status_code == 204:
            misses += 1
            if misses > 6:  # an hour with nothing to do: the project is finished
                return
            await sim_sleep(10 * 60)  # others' expired leases may free tasks up
            continue
        if r.status_code == 403:
            return  # suspended
        if r.status_code == 429:  # the claim limit (D51): slow down, as a person would
            await sim_sleep(60)
            continue
        r.raise_for_status()
        misses = 0
        a = r.json()
        p = PROFILES[behavior]
        seconds = rng.uniform(*p["seconds"])
        await sim_sleep(seconds)
        payload = a["task"]["payload"]
        if payload["response_a"] == payload["response_b"] and rng.random() < p["flags"]:
            (await c.post(f"/tasks/{a['task']['id']}/flag", headers=headers, json={"reason": BROKEN_REASON})).raise_for_status()
            continue
        better = truth[payload["item"]]
        choice = better if rng.random() < p["accuracy"] else ("B" if better == "A" else "A")
        answer = {"choice": choice}
        if rng.random() < p["reason"]:
            generic = p["paste"] and not p.get("notes")
            answer["rationale"] = PASTED_REASON.format(choice=choice) if generic else rng.choice(TYPED_REASONS)
        # What the browser would report (AB11): a paster switches to another window and starts within seconds.
        signals = {"pasted": p["paste"], "content_viewed": True,
                   "first_input_ms": int(rng.uniform(0.5, 2) * 1000 if p["paste"] else seconds * rng.uniform(300, 700)),
                   "focus_changes": rng.randint(1, 2) if p["paste"] else int(rng.random() < 0.1)}
        s = await c.post(f"/assignments/{a['id']}/submit", headers=headers, json={"answer": answer, "client_signals": signals})
        if s.status_code == 403:
            return
        if s.status_code == 409:
            continue  # lease expired during a long pause
        s.raise_for_status()
        progress.answered += 1
        progress.by_vendor[vendor] = progress.by_vendor.get(vendor, 0) + 1
        until_break -= seconds
        if until_break <= 0:
            await sim_sleep(rng.uniform(5, 25) * 60)
            until_break = rng.uniform(20, 40) * 60


async def vendor_admin(c, vendor: str, done: asyncio.Event) -> None:
    """Brightline's admin checks for open cases and, after reviewing, suspends the worker."""
    users = (await c.get("/auth/demo-users")).json()
    headers = await as_person(c, next(u["display_name"] for u in users if u["role"] == "vendor_admin" and u["org_name"] == vendor))
    seen: dict[str, float] = {}
    loop = asyncio.get_running_loop()
    while not done.is_set():
        for case in (await c.get("/cases?status=open", headers=headers)).json():
            if case["subject_type"] != "worker":
                continue  # a vendor case about Brightline itself is data ops' to decide, after Brightline responds
            seen.setdefault(case["id"], loop.time())
            if loop.time() - seen[case["id"]] >= REVIEW_DELAY_MINUTES * 60 / SPEED:
                detail = (await c.get(f"/cases/{case['id']}", headers=headers)).json()
                reason = "Reviewed the evidence: " + " ".join(f["observation"] for f in detail["flags"])
                await c.post(f"/cases/{case['id']}/decision", headers=headers, json={"decision": "suspend", "reason": reason[:1000]})
                print(f"  {vendor} admin suspends {case['subject_name']} after reviewing the case", flush=True)
        await sim_sleep(10 * 60)


def looks_pasted(rationale: str | None) -> bool:
    """The simulated researcher recognizes the generic pasted reason. A real reviewer judges by
    reading; this one knows which simulated workers paste (docs/NOT-SOLVED.md, Modeled)."""
    return bool(rationale) and rationale.startswith(PASTED_REASON.split("{choice}")[0])


async def spot_checker(c, project_id, truth, done: asyncio.Event) -> None:
    """The researcher's spot checks (DC4, D40): a batch every few minutes, each answer judged for
    quality (against the right answer) and, separately, for abuse. The queue mixes in the audit
    sample and answers detection flagged; the researcher can't tell which are which."""
    users = (await c.get("/auth/demo-users")).json()
    researcher = await as_person(c, next(u["display_name"] for u in users if u["role"] == "researcher"))
    reviewed = abusive = retired = 0
    alerting = False
    while not done.is_set():
        await sim_sleep(SPOT_CHECK_EVERY_MINUTES * 60)
        health = (await c.get(f"/projects/{project_id}/task-health", headers=researcher)).json()
        alert = health["agreement_alert"]
        if alert and not alerting:  # each time an alert appears
            same = sum(t["payload"]["response_a"] == t["payload"]["response_b"] for t in alert["tasks"])
            print(f"  Agreement alert: {alert['agreement'] * 100:.0f}% on a batch of {alert['batch_tasks']} tasks, "
                  f"{alert['others'] * 100:.0f}% on the rest; {same} of its {len(alert['tasks'])} worst tasks have two identical "
                  f"answers", flush=True)
        alerting = alert is not None
        if AUTOPILOT:
            # Tasks workers flagged as broken: retire them when the two answers really are the same.
            for t in health["broken"]:
                if t["broken_status"] == "paused":
                    same = t["payload"]["response_a"] == t["payload"]["response_b"]
                    await c.post(f"/tasks/{t['task_id']}/broken-decision", headers=researcher, json={
                        "decision": "retire" if same else "restore",
                        "reason": "Both answers are identical." if same else "The answers differ; the task is fine."})
                    retired += same
        for item in (await c.get(f"/projects/{project_id}/review-queue", headers=researcher, params={"n": SPOT_CHECK_BATCH})).json():
            pasted = looks_pasted(item["answer"].get("rationale"))
            right = item["answer"]["choice"] == truth[item["payload"]["item"]]
            body = {"verdict": "accepted" if right and not pasted else "rejected", "looks_abusive": pasted,
                    "reason": None if right and not pasted else
                    ("The reason is generic and doesn't engage with either answer." if pasted else "The other answer is better.")}
            r = await c.post(f"/submissions/{item['submission_id']}/review", headers=researcher, json=body)
            if r.status_code == 201:
                reviewed += 1
                abusive += pasted
    print(f"  Researcher spot-checked {reviewed} answers and judged {abusive} abusive", flush=True)
    if retired:
        print(f"  Researcher retired {retired} tasks workers flagged as broken", flush=True)


# Vendor review (DC8, D44): Northwind checks its workers' answers before they reach Anthropic.
REVIEWING_VENDOR = "Northwind Data"
VENDOR_REVIEWER = "Hana Kowalski"
REVIEW_EVERY_MINUTES = 3  # simulated time between the reviewer's batches
CATCHES = 0.7  # share of wrong picks the reviewer notices and corrects; she never makes a right one wrong
LEFT_FOR_YOU = 10  # with DEMO_AUTOPILOT=false, the newest answers she leaves waiting


async def vendor_reviewer(c, project_id, truth, done: asyncio.Event) -> None:
    """Northwind's admin turns review on; its reviewer works through the queue all project. The
    simulated reviewer knows the right answers the way the simulated workers do (docs/NOT-SOLVED.md)."""
    users = (await c.get("/auth/demo-users")).json()
    admin = await as_person(c, next(u["display_name"] for u in users if u["role"] == "vendor_admin" and u["org_name"] == REVIEWING_VENDOR))
    mine = (await c.get(f"/projects/{project_id}/my-allocation", headers=admin)).json()
    (await c.put(f"/allocations/{mine['id']}/vendor-review", headers=admin, json={"on": True})).raise_for_status()
    print(f"  {REVIEWING_VENDOR} reviews its workers' answers before they reach Anthropic", flush=True)
    reviewer = await as_person(c, VENDOR_REVIEWER)
    rng = random.Random(SEED + 2)
    reviewed = corrected = 0
    while True:
        items = (await c.get("/vendor-review/queue", headers=reviewer, params={"n": 50})).json()["items"]
        if done.is_set() and (not items or not AUTOPILOT):
            break
        if not AUTOPILOT:
            # By hand, the newest answers on ordinary tasks are left for you to review as Hana Kowalski.
            ordinary = [i for i in items if i["payload"]["response_a"] != i["payload"]["response_b"]]
            left = {i["submission_id"] for i in ordinary[-LEFT_FOR_YOU:]}
            items = [i for i in items if i["submission_id"] not in left]
        for item in items:
            right = truth[item["payload"]["item"]]
            if item["answer"]["choice"] != right and rng.random() < CATCHES:
                body = {"decision": "corrected", "answer": {**item["answer"], "choice": right}, "note": "The other answer is better."}
                corrected += 1
            else:
                body = {"decision": "accepted"}
            if (await c.post(f"/vendor-review/{item['submission_id']}", headers=reviewer, json=body)).status_code == 201:
                reviewed += 1
        await sim_sleep(REVIEW_EVERY_MINUTES * 60)
    print(f"  {REVIEWING_VENDOR}'s reviewer checked {reviewed} answers and corrected {corrected}", flush=True)


# Vendor oversight (AB2, AB5): how each vendor answers a vendor case, and what data ops does next.
VENDOR_RESPONSES = {
    "Brightline Annotation": "One worker caused the drop; we suspended her after reviewing her case and are re-checking how we onboard.",
    "Kestrel Labs": "We've retired both environments and now have a second builder review every grader before we submit.",
}
VENDOR_CASE_DELAY_MINUTES = 15  # simulated time between each step of a vendor case


async def vendor_oversight(c, project_id, done: asyncio.Event) -> None:
    """Data ops shares each vendor case the platform opens, the vendor responds, and data ops acts:
    a vendor whose environments keep being quarantined is paused on that brief; anything else is
    cleared after the response. (One bad worker rarely moves a whole vendor's accuracy enough for
    quality-volume-v1; see docs/LIMITATIONS.md.)"""
    users = (await c.get("/auth/demo-users")).json()
    ops = await as_person(c, next(u["display_name"] for u in users if u["role"] == "data_ops"))
    admins = {u["org_name"]: u["display_name"] for u in users if u["role"] == "vendor_admin"}
    handled = set()
    while not done.is_set():
        await sim_sleep(VENDOR_CASE_DELAY_MINUTES * 60)
        for case in (await c.get("/cases", headers=ops, params={"status": "open"})).json():
            if case["subject_type"] != "vendor" or case["id"] in handled:
                continue
            vendor = case["vendor_name"]
            print(f"  Vendor rule: {vendor}: {case['observations'][0]}", flush=True)
            await c.post(f"/cases/{case['id']}/share", headers=ops)
            print(f"  Data ops asks {vendor} to respond", flush=True)
            await sim_sleep(VENDOR_CASE_DELAY_MINUTES * 60)
            admin = await as_person(c, admins[vendor])
            await c.post(f"/cases/{case['id']}/responses", headers=admin, json={"text": VENDOR_RESPONSES.get(vendor, "We're looking into it.")})
            print(f"  {vendor} responds", flush=True)
            await sim_sleep(VENDOR_CASE_DELAY_MINUTES * 60)
            detail = (await c.get(f"/cases/{case['id']}", headers=ops)).json()
            if "quarantined" in case["observations"][0] and detail["project_id"]:
                r = await c.post(f"/cases/{case['id']}/actions", headers=ops, json={
                    "action": "pause", "project_id": detail["project_id"],
                    "reason": "Paused on this brief until the second grader review shows in a new batch."})
                r.raise_for_status()
                await c.post(f"/cases/{case['id']}/decision", headers=ops, json={"decision": "resolve", "reason": "Paused on the brief."})
                print(f"  Data ops pauses {vendor} on the brief until its new grader review shows results", flush=True)
            else:
                await c.post(f"/cases/{case['id']}/decision", headers=ops, json={
                    "decision": "clear", "reason": "The vendor's response covers it; nothing to act on."})
                print(f"  Data ops clears {vendor}'s case after its response", flush=True)
            handled.add(case["id"])


async def data_ops(c, project_id, progress: Progress, done: asyncio.Event) -> None:
    """The researcher exports every half hour; with autopilot, data ops cleans up after a suspension."""
    users = (await c.get("/auth/demo-users")).json()
    ops = await as_person(c, next(u["display_name"] for u in users if u["role"] == "data_ops"))
    researcher = await as_person(c, next(u["display_name"] for u in users if u["role"] == "researcher"))
    cleaned = set()
    loop = asyncio.get_running_loop()
    decided_at: dict[str, float] = {}
    next_export, exports = loop.time() + EXPORT_EVERY_MINUTES * 60 / SPEED, 0
    while not done.is_set():
        if loop.time() >= next_export:
            exports += 1
            d = (await c.post(f"/projects/{project_id}/exports", headers=researcher)).json()
            print(f"  Researcher exports dataset {exports}: {d['row_count']} released answers"
                  f" ({d['provisional']} provisional)", flush=True)
            next_export += EXPORT_EVERY_MINUTES * 60 / SPEED
        for case in (await c.get("/cases?status=decided", headers=ops)).json() if AUTOPILOT else []:
            if case["subject_type"] == "worker" and case["decision"] in ("suspend", "remove") and case["id"] not in cleaned:
                decided_at.setdefault(case["id"], loop.time())
                if loop.time() - decided_at[case["id"]] >= CLEANUP_DELAY_MINUTES * 60 / SPEED:
                    r = (await c.post("/cleanups", headers=ops, json={"case_id": case["id"], "mode": "relabel"})).json()
                    print(f"  Data ops cleans up: {r['removed']} answers removed, tasks reopened; "
                          f"{r['datasets_affected']} earlier dataset(s) contained them", flush=True)
                    cleaned.add(case["id"])
        await sim_sleep(10 * 60)
    # The last answers need the release window to pass before they can go out.
    await sim_sleep((RELEASE_CAP_MINUTES + 5) * 60)
    d = (await c.post(f"/projects/{project_id}/exports", headers=researcher)).json()
    print(f"  Researcher exports the final dataset: {d['row_count']} released answers"
          f" ({d['provisional']} provisional)", flush=True)
    r = (await c.get(f"/projects/{project_id}/release", headers=researcher)).json()
    print(f"  Release: {r['verified']} verified, {r['provisional']} provisional, "
          f"{sum(r['waiting'].values())} still waiting, of {r['accepted']} accepted", flush=True)


async def report(c, project_id) -> None:
    users = (await c.get("/auth/demo-users")).json()
    ops = await as_person(c, next(u["display_name"] for u in users if u["role"] == "data_ops"))
    q = (await c.get(f"/projects/{project_id}/quality", headers=ops)).json()
    pct = lambda x: "  —" if x is None else f"{x * 100:4.0f}%"  # noqa: E731
    for w in (await c.get("/vendors/environment-work", headers=ops)).json():
        print(f"  Environment work, {w['vendor_name']}: {w['submitted']} versions submitted, {w['passed_checks']} passed the "
              f"sandbox's checks, {w['approved']} approved, {w['quarantined']} quarantined", flush=True)
        if w["episodes"]:
            print(f"  In training, {w['vendor_name']}: {w['episodes']:,} episodes, {pct(w['solve_rate']).strip()} solved, "
                  f"{w['flagged']} attempts flagged as hacks, {w['recalls']} recalls", flush=True)
    d = (await c.get("/detection-quality", headers=ops)).json()
    for r in d["rules"]:
        if r["detector"] == "rules-v1" and r["findings"]:
            print(f"  Detection, {r['rule_version']} ({r['mode']}): {r['findings']} findings on {r['subjects']} workers, "
                  f"{r['abusive']} of {r['reviewed']} reviewed flags judged abusive", flush=True)
    if d["audited"]:
        print(f"  Audit sample: {d['audited_abusive']} of {d['audited']} audited answers from unflagged workers judged abusive",
              flush=True)
    for v in (await c.get(f"/projects/{project_id}/vendors", headers=ops)).json()["vendors"]:
        if v["vendor_reviewed"]:
            print(f"  Vendor review, {v['vendor_name']}: {v['vendor_corrected']} of {v['vendor_reviewed']} answers corrected; gold accuracy "
                  f"{pct(v['gold_accuracy']).strip()} on what it submitted, {pct(v['workers_gold_accuracy']).strip()} on its workers' own answers",
                  flush=True)
    for x in (await c.get(f"/projects/{project_id}/escalations", headers=ops)).json():
        print(f"  Escalation on {x['handle']} ({x['vendor_name']}) after a {x['rule']} signal: "
              f"{x['outcome'] or 'still open'}, {x['gold_right_since']} of {x['gold_since']} later gold answers right", flush=True)
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
        progress.expected_by_vendor = {v: round((TASKS - gold) * float(s)) * 2 + gold * 2 for v, s in SHARES.items()}
        print(f"Running {len(workers)} simulated workers across {len(SHARES)} vendors", flush=True)

        async def ticker():
            while True:
                await asyncio.sleep(5)
                print(f"  {progress.answered}/{progress.expected} answers", flush=True)

        tick = asyncio.create_task(ticker())
        done = asyncio.Event()
        helpers = [asyncio.create_task(data_ops(c, project_id, progress, done)), asyncio.create_task(environments_and_training(c)),
                   asyncio.create_task(harbor_act(c)), asyncio.create_task(generation_act(c)),
                   asyncio.create_task(spot_checker(c, project_id, truth, done)),
                   asyncio.create_task(vendor_reviewer(c, project_id, truth, done))]
        if AUTOPILOT:
            helpers.append(asyncio.create_task(vendor_admin(c, "Brightline Annotation", done)))
            helpers.append(asyncio.create_task(vendor_oversight(c, project_id, done)))
        rngs = random.Random(SEED)
        await asyncio.gather(*(run_worker(c, project_id, truth, v, n, p, progress, random.Random(rngs.random()))
                               for v, n, p in workers))
        tick.cancel()
        done.set()
        await asyncio.gather(*helpers)
        print(f"Done: {progress.answered} answers", flush=True)
        await report(c, project_id)


if __name__ == "__main__":
    asyncio.run(main())
