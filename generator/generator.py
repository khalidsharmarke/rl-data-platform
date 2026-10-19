"""The code task generator (EC7, A21, D48): a separate service that is a client of the platform.

It signs in with its own API token, like any vendor's tooling, and fills every environment brief
allocated to it with tasks generated from the families in families.py, each carrying probes.
Every version goes through the same checks, review, catalog and quarantine as a vendor's.

It keeps no state of its own; the platform's records are the state:
- a task whose content matches an environment already on the brief is never resubmitted
  (environment names carry a hash of the content);
- a family whose last two checked versions both failed is stopped, and generates nothing more;
- so is a family a researcher stopped by sending back a sampled version, until it's resumed (D49).

When a batch is complete, the generator tells the platform, which draws the sample researchers review.
"""

import base64
import hashlib
import os
import random
import time
from datetime import UTC, datetime

import families
import rlp

API = os.environ.get("API_URL", "http://api:8000")
BATCH_SIZE = int(os.environ.get("GENERATOR_BATCH_SIZE", "9"))
STOP_AFTER = 2  # consecutive failed versions that stop a family
FINISHED = {"failed": False, "in_review": True, "approved": True, "quarantined": True}


def content_name(family: str, difficulty: str, task: families.Task) -> str:
    digest = hashlib.sha256(repr(sorted(task.items())).encode()).hexdigest()
    return f"{family}-{difficulty}-{digest[:8]}"


def stopped_families(api: rlp.Api, brief_id: str) -> set[str]:
    """Families a researcher stopped, and those whose last STOP_AFTER checked versions on this brief all failed."""
    stopped = {s["family"] for s in api.call("GET", f"/projects/{brief_id}/generated")["stopped"]}
    outcomes: dict[str, list[bool]] = {}
    for v in api.call("GET", f"/versions?project_id={brief_id}"):  # oldest first
        if v["status"] in FINISHED:
            outcomes.setdefault(v["environment_name"].split("-")[0], []).append(FINISHED[v["status"]])
    return stopped | {f for f, results in outcomes.items() if len(results) >= STOP_AFTER and not any(results[-STOP_AFTER:])}


def body(task: families.Task, family: str, difficulty: str, batch: str) -> dict:
    return {"test_command": families.TEST_COMMAND, "generated": {"family": family, "difficulty": difficulty, "batch": batch},
            "files": [{"path": p, "part": p.split("/")[0], "content_b64": base64.b64encode(t.encode()).decode()} for p, t in sorted(task.items())]}


def fill(api: rlp.Api, brief: dict) -> None:
    names = {e["name"] for e in api.call("GET", f"/environments?project_id={brief['id']}")}
    wanted = min(BATCH_SIZE, brief["target_volume"] - len(names))
    if wanted <= 0 or not set(families.FAMILIES) - stopped_families(api, brief["id"]):
        return
    batch = datetime.now(UTC).strftime("batch-%Y%m%d-%H%M%S")
    rng = random.Random(batch)
    print(f"{brief['name']}: generating {batch}, {wanted} tasks", flush=True)
    submitted, announced, turn, made = 0, set(), 0, dict.fromkeys(families.FAMILIES, 0)
    while submitted < wanted:
        stopped = stopped_families(api, brief["id"])
        for family in stopped - announced:
            print(f"  family {family} is stopped", flush=True)
        announced |= stopped
        active = [f for f in families.FAMILIES if f not in stopped]
        if not active:
            print("  every family is stopped; fix the templates", flush=True)
            break
        family = active[turn % len(active)]
        difficulty = families.DIFFICULTIES[made[family] % len(families.DIFFICULTIES)]  # each family cycles easy, medium, hard
        turn += 1
        made[family] += 1
        task = families.make(family, difficulty, rng)
        name = content_name(family, difficulty, task)
        if name in names:
            continue  # the same content is never submitted twice
        version, checks = rlp.submit(api, body(task, family, difficulty, batch), brief=brief["id"], name=name)
        names.add(name)
        submitted += 1
        failed = [c["detail"] for c in checks if not c["passed"]]
        if version["status"] == "failed":
            print(f"  {name}: failed: {failed[0] if failed else 'see its checks'}", flush=True)
        else:
            print(f"  {name}: passed all {len(checks)} checks", flush=True)
    if submitted:
        ready = api.call("POST", f"/projects/{brief['id']}/generated-batches/{batch}/ready")
        print(f"  {batch} is ready: {ready['passed_checks']} of {ready['versions']} passed the checks, {ready['sampled']} drawn for review", flush=True)


def main() -> None:
    api = rlp.Api(API, os.environ["GENERATOR_TOKEN"])
    while True:
        try:
            for brief in api.call("GET", "/projects"):
                if brief["kind"] == "environment" and not brief["onboarding"] and brief["status"] == "active":
                    fill(api, brief)
        except (OSError, SystemExit) as e:  # the API restarting, or a refused call: try again next round
            print(f"generator: {e}", flush=True)
        time.sleep(10)


if __name__ == "__main__":
    main()
