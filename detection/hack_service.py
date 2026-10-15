"""The reward-hack detector as a service (TO3, D35): a separate process that talks to the
platform only through its contract, like the worker detection service (service.py).

- GET  /hack-detection/episodes?after=N   episodes in sequence order
- GET  /hack-detection/labels             decided environment cases
- GET  /hack-detection/progress           how far it got before it last stopped
- POST /hack-detection/findings           findings with a severity, plus how far it has read

On start it replays the stream up to its recorded progress without reporting, to rebuild its
state, then follows new episodes.
"""

import asyncio
import logging
import os

import httpx

from hacks import DETECTOR, HackDetector

API = os.environ.get("API_URL", "http://api:8000")
TOKEN = os.environ.get("DETECTION_TOKEN", "")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "1"))
BATCH = 2000

log = logging.getLogger("hacks")


async def fetch(c: httpx.AsyncClient, path: str, **params) -> dict:
    r = await c.get(path, params=params)
    r.raise_for_status()
    return r.json()


async def run(c: httpx.AsyncClient) -> None:
    d = HackDetector()
    seen_labels = 0
    progress = (await fetch(c, "/hack-detection/progress"))["processed_through"]
    after = 0
    while after < progress:  # replay without reporting
        episodes = [e for e in (await fetch(c, "/hack-detection/episodes", after=after, limit=BATCH))["episodes"] if e["seq"] <= progress]
        if not episodes:
            break
        for e in episodes:
            d.process(e)
        after = episodes[-1]["seq"]
    d.processed_through = progress
    log.info("%s started: replayed through episode %s", DETECTOR, progress)
    while True:
        labels = (await fetch(c, "/hack-detection/labels"))["labels"]
        for label in labels[seen_labels:]:
            d.apply_label(label)
        seen_labels = len(labels)
        episodes = (await fetch(c, "/hack-detection/episodes", after=d.processed_through, limit=BATCH))["episodes"]
        if not episodes:
            await asyncio.sleep(POLL_SECONDS)
            continue
        findings = [f for e in episodes for f in d.process(e)]
        r = await c.post("/hack-detection/findings", json={
            "detector": DETECTOR, "processed_through": d.processed_through, "findings": [f.as_json() for f in findings]})
        r.raise_for_status()
        for f in findings:
            log.info("finding: %s %s on %s in %s (%s)", f.severity, f.rule, f.version_id[:8], f.run_label, f.observation)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with httpx.AsyncClient(base_url=API, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as c:
        while True:
            try:
                await run(c)
            except httpx.HTTPError as exc:
                log.warning("platform unavailable (%s); retrying", exc)
                await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
