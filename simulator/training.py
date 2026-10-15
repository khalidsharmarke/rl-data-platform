"""A stand-in for training infrastructure (A23, EC6): it polls the catalog and pulls each
newly approved environment version, exactly once, under a run label. It trains nothing.

It talks to the platform only through the catalog, with its own token, like the real thing
would. Runs as its own service in `make demo`.
"""

import os
import time

import httpx

API = os.environ.get("API_URL", "http://api:8000")
TOKEN = os.environ.get("TRAINING_TOKEN", "")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "2"))


def main() -> None:
    pulled: set[str] = set()
    run = 0
    with httpx.Client(base_url=API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as c:
        while True:
            try:
                fresh = [e for e in c.get("/catalog").json() if e["version_id"] not in pulled]
                if fresh:
                    run += 1
                    label = f"rl-run-{run:03d}"
                    for e in fresh:
                        r = c.post(f"/catalog/{e['version_id']}/pull", json={"run_label": label})
                        if r.status_code == 200:
                            files = ", ".join(f["path"] for f in r.json()["files"])
                            print(f"training {label}: pulled {e['environment_name']} v{e['number']} ({files})", flush=True)
                        pulled.add(e["version_id"])
            except (httpx.HTTPError, ValueError):
                pass  # the platform isn't up yet
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
