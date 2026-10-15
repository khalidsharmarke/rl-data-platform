"""Worker abuse rules (AB1), pluggable and versioned (D15, N19).

Each rule looks at a worker's most recent answers on a project and either returns nothing or
an observation: a plain statement of what was seen, plus the evidence behind it. Vendors see
observations and evidence, never thresholds or rule names (N17). Low quality on its own is
not abuse (PRD PD4): none of these rules looks at accuracy.

Severity (D32): one rule on its own is a watch finding, which the platform answers by
measuring the worker more closely. Two or more rules agreeing make it a case. A rule built
only on what the browser reports (pasting) can be forged (A32), so it never counts toward a
case by itself.

Modes (D27, D41): the platform decides which rule versions are live. A rule in shadow mode
still runs and its findings are reported, but it never counts toward a live finding's
severity, so a rule that hasn't proven itself can't turn a watch into a case.
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

WINDOW = 10
READING_WORDS_PER_SECOND = 4.0


@dataclass(frozen=True)
class Answer:
    submission_id: str
    time_on_task_ms: int
    words: int
    pasted: bool
    rationale: str | None


@dataclass(frozen=True)
class Finding:
    rule: str
    version: str
    observation: str
    evidence: dict


def reading_seconds(words: int) -> float:
    return words / READING_WORDS_PER_SECOND


def too_fast(window: list[Answer]) -> Finding | None:
    fast = [a for a in window if a.time_on_task_ms / 1000 < 0.6 * reading_seconds(a.words)]
    if len(fast) < 6:
        return None
    median = sorted(a.time_on_task_ms for a in window)[len(window) // 2] / 1000
    typical = sorted(reading_seconds(a.words) for a in window)[len(window) // 2]
    return Finding("speed", "speed-v1",
                   f"{len(fast)} of the last {len(window)} answers were submitted faster than the task can be read "
                   f"(median {median:.0f} s; reading the task takes about {typical:.0f} s).",
                   {"submission_ids": [a.submission_id for a in fast], "median_seconds": round(median, 1), "reading_seconds": round(typical, 1)})


def pasted(window: list[Answer]) -> Finding | None:
    hits = [a for a in window if a.pasted and a.rationale]
    if len(hits) < 5:
        return None
    return Finding("paste", "paste-v1",
                   f"Reason text was pasted rather than typed in {len(hits)} of the last {len(window)} answers.",
                   {"submission_ids": [a.submission_id for a in hits]})


def normalize(text: str) -> str:
    text = re.sub(r"\b(answer )?[ab]\b", "", text.lower())
    return re.sub(r"[^a-z]+", " ", text).strip()


def duplicates(window: list[Answer]) -> Finding | None:
    reasons = [normalize(a.rationale) for a in window if a.rationale]
    if not reasons:
        return None
    text, count = Counter(reasons).most_common(1)[0]
    if count < 5 or len(text.split()) < 6:
        return None
    ids = [a.submission_id for a in window if a.rationale and normalize(a.rationale) == text]
    return Finding("duplicate", "duplicate-v1",
                   f"The same reason, word for word, was given for {count} of the last {len(window)} answers to different questions.",
                   {"submission_ids": ids})


def terse(window: list[Answer]) -> Finding | None:
    """New, and in shadow until it proves itself: reasons too short to engage with the task, on
    almost every answer."""
    short = [a for a in window if a.rationale is not None and len(a.rationale.split()) <= 5]
    if len(short) < 8:
        return None
    return Finding("terse", "terse-v1",
                   f"{len(short)} of the last {len(window)} reasons were five words or fewer.",
                   {"submission_ids": [a.submission_id for a in short]})


RULES: list[Callable[[list[Answer]], Finding | None]] = [too_fast, pasted, duplicates, terse]


# Rules that rest only on signals the browser reports.
CLIENT_ONLY = {"paste"}


def evaluate(window: list[Answer]) -> list[Finding]:
    if len(window) < WINDOW:
        return []
    return [f for rule in RULES if (f := rule(window)) is not None]


def severity(findings: list[Finding]) -> str:
    """'case' when at least two rules agree and one of them rests on server-side evidence."""
    rules = {f.rule for f in findings}
    return "case" if len(rules) >= 2 and rules - CLIENT_ONLY else "watch"


def severities(findings: list[Finding], live: set[str] | None) -> dict[str, str]:
    """Each finding's severity by rule version. Live findings are judged on live rules alone; a
    shadow finding gets the severity it would have if its rule were live, for measuring it.
    live=None means every rule is live."""
    on = [f for f in findings if live is None or f.version in live]
    out = {f.version: severity(on) for f in on}
    for f in findings:
        out.setdefault(f.version, severity(on + [f]))
    return out
