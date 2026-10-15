"""The detection service (AB13, D23): a separate process that talks to the platform only
through its contract.

- GET  /detection/events?after=N   submission events, in commit order (D24)
- GET  /detection/labels?after=N   case decisions, so a decided worker is judged afresh (D20)
- GET  /detection/outcomes?after=N reviewers' abuse judgements and cleared escalations (D40)
- GET  /detection/rules            which rule versions are live; the rest run in shadow (D41)
- GET  /detection/progress         how far this detector got before it last stopped
- POST /detection/findings         findings with a severity, plus how far it has processed

It keeps its state in memory. On start it replays the event stream up to its recorded
progress without reporting anything, which rebuilds each worker's recent answers; then it
follows new events. Reporting progress even when nothing was found is what lets the
platform's release stage move on (D25).
"""

import asyncio
import logging
import os

import httpx

from detector import DETECTOR, Detector

API = os.environ.get("API_URL", "http://api:8000")
TOKEN = os.environ.get("DETECTION_TOKEN", "")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "1"))
BATCH = 500

log = logging.getLogger("detection")


async def fetch(c: httpx.AsyncClient, path: str, **params) -> dict:
    r = await c.get(path, params=params)
    r.raise_for_status()
    return r.json()


async def catch_up_labels(c: httpx.AsyncClient, d: Detector, after: int) -> int:
    while True:
        labels = (await fetch(c, "/detection/labels", after=after))["labels"]
        if not labels:
            return after
        for label in labels:
            d.apply_label(label)
            after = label["seq"]


async def catch_up_outcomes(c: httpx.AsyncClient, d: Detector, after: int) -> int:
    """A cleared escalation is treated like a decision: the worker is judged afresh from there.
    Abuse judgements are counted; the platform measures precision from them (D40)."""
    while True:
        outcomes = (await fetch(c, "/detection/outcomes", after=after))["outcomes"]
        if not outcomes:
            return after
        for o in outcomes:
            if o["kind"] == "escalation":
                d.apply_cleared(o)
            else:
                d.judged[o["outcome"]] += 1
            after = o["seq"]


async def read_modes(c: httpx.AsyncClient, d: Detector) -> None:
    d.live = {version for version, mode in (await fetch(c, "/detection/rules", detector=DETECTOR))["modes"].items() if mode == "live"}


async def replay(c: httpx.AsyncClient, d: Detector, through: int) -> None:
    """Rebuild state from the events already processed, without reporting them again."""
    after = 0
    while after < through:
        events = (await fetch(c, "/detection/events", after=after, limit=BATCH))["events"]
        events = [e for e in events if e["id"] <= through]
        if not events:
            break
        for e in events:
            d.process(e)
        after = events[-1]["id"]
    d.processed_through = through


async def run(c: httpx.AsyncClient) -> None:
    d = Detector()
    progress = (await fetch(c, "/detection/progress"))["processed_through"]
    await read_modes(c, d)
    label_cursor = await catch_up_labels(c, d, 0)
    outcome_cursor = await catch_up_outcomes(c, d, 0)
    await replay(c, d, progress)
    log.info("%s started: replayed through event %s; live rules: %s", DETECTOR, progress, ", ".join(sorted(d.live)))
    while True:
        await read_modes(c, d)
        label_cursor = await catch_up_labels(c, d, label_cursor)
        outcome_cursor = await catch_up_outcomes(c, d, outcome_cursor)
        events = (await fetch(c, "/detection/events", after=d.processed_through, limit=BATCH))["events"]
        if not events:
            await asyncio.sleep(POLL_SECONDS)
            continue
        reports = [r for e in events for r in d.process(e)]
        r = await c.post("/detection/findings", json={
            "detector": DETECTOR, "processed_through": d.processed_through,
            "findings": [rep.as_json() for rep in reports],
        })
        r.raise_for_status()
        for rep in reports:
            log.info("finding: %s %s on worker %s (%s)", rep.severity, rep.rule, rep.worker_id[:8], rep.observation)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with httpx.AsyncClient(base_url=API, headers=headers, timeout=30) as c:
        while True:
            try:
                await run(c)
            except httpx.HTTPError as exc:
                # The platform isn't up yet, or restarted: start over from recorded progress.
                log.warning("platform unavailable (%s); retrying", exc)
                await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
