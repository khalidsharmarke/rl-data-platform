"""A stand-in for training infrastructure (A23, EC6, TO1, build decision B6).

It pulls each newly approved environment version from the catalog and runs a modeled training
run over the approved versions it holds, sending one batch of episodes per training step. It
trains nothing, and no model attempts anything: each episode's outcome is drawn from a modeled
policy.

- Honest attempts read the task and fix the code. Their chance of passing rises as the run goes
  on, as a real model's would while it learns.
- On an environment its profile marks as hackable, the policy discovers partway through the run
  that a shortcut scores full marks, and uses it more and more. The demo's one hackable
  environment has a grader that only checks the empty string; a sandbox test proves that
  `return ""` really passes it (backend/tests/test_sandbox_runs.py). The hack is planted, not
  found: see docs/NOT-SOLVED.md.

Each step it re-reads the catalog, so a version quarantined mid-run stops being sampled, and
reads recalls (TO8, D37). For each run a recall names, it "rolls back" to the last checkpoint
before the first flagged attempt (or before the version first appeared, if nothing was
flagged) and reports that. Nothing is rolled back: there's no model. It talks to the platform
only through its token: the catalog, the episode intake and recalls.
"""

import os
import random
import time

import httpx

API = os.environ.get("API_URL", "http://api:8000")
TOKEN = os.environ.get("TRAINING_TOKEN", "")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "2"))
STEPS = int(os.environ.get("TRAINING_STEPS", "60"))
STEP_SECONDS = float(os.environ.get("STEP_SECONDS", "1"))
EPISODES_PER_STEP = int(os.environ.get("EPISODES_PER_STEP", "6"))
STEP_SIZE = 100  # training steps between batches, so step numbers look like a real run's
CHECKPOINT_EVERY = 500  # training steps between checkpoints

# Environments the modeled policy can hack, and how far into a run it discovers the shortcut.
HACKABLE = {"slugify edge cases": 0.35}

HONEST = ("read task/README.md\nread task/slug.py\nrun tests: 1 failed (IndexError on '')\n"
          "edit task/slug.py: return \"-\".join(w.lower() for w in text.split())\nrun tests: {result}")
HACK = "read task/README.md\nedit task/slug.py: return \"\"\nrun tests: passed"


def episode(run: str, step: int, entry: dict, i: int, rng: random.Random) -> dict:
    progress = step / STEPS
    found = HACKABLE.get(entry["environment_name"])
    exploit_share = 0.0 if found is None or progress < found else min(0.95, 0.3 + (progress - found) * 3)
    base = {"episode_id": f"{run}-s{step}-{entry['version_id'][:8]}-{i}", "version_id": entry["version_id"], "step": step * STEP_SIZE}
    if rng.random() < exploit_share:
        return {**base, "reward": 1.0, "passed": True, "length": rng.randint(3, 6), "touched": ["task/slug.py"], "transcript": HACK}
    passed = rng.random() < 0.15 + 0.6 * progress
    return {**base, "reward": 1.0 if passed else 0.0, "passed": passed, "length": rng.randint(25, 60), "touched": ["task/slug.py"],
            "transcript": HONEST.format(result="passed" if passed else "1 failed")}


class Recalls:
    """Reads recalls and reports what training did with each run it ran."""

    def __init__(self) -> None:
        self.after = 0
        self.mine: set[str] = set()

    def answer(self, c: httpx.Client) -> None:
        for recall in c.get("/recalls", params={"after": self.after}).json():
            self.after = recall["id"]
            for r in recall["runs"]:
                if r["run_label"] not in self.mine or r["first_step"] is None:
                    continue
                bad_from = r["first_flagged_step"] if r["first_flagged_step"] is not None else r["first_step"]
                checkpoint = max(0, (bad_from - 1) // CHECKPOINT_EVERY * CHECKPOINT_EVERY)
                c.post(f"/recalls/{recall['id']}/reports", json={
                    "run_label": r["run_label"], "action": "rolled_back", "checkpoint_step": checkpoint,
                    "note": f"Restored the checkpoint before step {bad_from:,}; {recall['environment_name']} is out of the run."})
                print(f"training {r['run_label']}: recall {recall['id']}: rolled back to step {checkpoint:,}", flush=True)


RECALLS = Recalls()


def run_training(c: httpx.Client, run: str) -> None:
    RECALLS.mine.add(run)
    rng = random.Random(run)
    print(f"training {run}: starting {STEPS} steps", flush=True)
    for step in range(STEPS):
        entries = c.get("/catalog").json()  # a version quarantined mid-run drops out here
        if entries:
            episodes = [episode(run, step, e, i, rng) for e in entries for i in range(EPISODES_PER_STEP)]
            c.post("/ingest/episodes", json={"run_label": run, "episodes": episodes}).raise_for_status()
        RECALLS.answer(c)
        time.sleep(STEP_SECONDS)
    print(f"training {run}: finished", flush=True)


def main() -> None:
    pulled: set[str] = set()
    runs = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as c:
        while True:
            try:
                catalog = c.get("/catalog").json()
                if any(e["version_id"] not in pulled for e in catalog):
                    # Something new was approved: start a run over everything approved, pulling each
                    # version under this run's label so lineage records what the run received.
                    runs += 1
                    run = f"rl-run-{runs:03d}"
                    for e in catalog:
                        r = c.post(f"/catalog/{e['version_id']}/pull", json={"run_label": run})
                        if r.status_code == 200:
                            files = ", ".join(f["path"] for f in r.json()["files"])
                            print(f"training {run}: pulled {e['environment_name']} v{e['number']} ({files})", flush=True)
                        pulled.add(e["version_id"])
                    run_training(c, run)
                RECALLS.answer(c)
            except (httpx.HTTPError, ValueError) as exc:
                print(f"training: platform unavailable ({exc}); retrying", flush=True)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
