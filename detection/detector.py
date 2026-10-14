"""The detection service's state: each worker's recent answers per project, and what it has
already reported (AB13, D23).

Pure logic, no I/O, so it can be tested on recorded events. The service feeds it events in
id order and case decisions (labels) as they arrive. After a decision on a worker, only the
answers that come after it are judged (D20): a cleared or reinstated worker isn't flagged
again on evidence a person has already weighed.
"""

from collections import deque
from dataclasses import dataclass, field

from rules import WINDOW, Answer, evaluate, severity

DETECTOR = "rules-v1"


@dataclass
class Report:
    worker_id: str
    vendor_id: str
    project_id: str
    rule: str
    rule_version: str
    severity: str
    observation: str
    evidence: dict
    through_event: int

    def as_json(self) -> dict:
        return self.__dict__.copy()


@dataclass
class Detector:
    windows: dict = field(default_factory=dict)  # (worker, project) -> deque of (event id, Answer)
    reported: dict = field(default_factory=dict)  # (worker, project) -> {rule: severity}
    decided_through: dict = field(default_factory=dict)  # worker -> last event id a decision covered
    processed_through: int = 0

    def apply_label(self, label: dict) -> None:
        """A person decided a case on this worker: start judging afresh from the next answer."""
        worker = label["worker_id"]
        through = label["through_event"] or 0
        self.decided_through[worker] = max(self.decided_through.get(worker, 0), through)
        for key in [k for k in self.windows if k[0] == worker]:
            self.windows[key] = deque((i, a) for i, a in self.windows[key] if i > through)
            self.reported.pop(key, None)

    def process(self, event: dict) -> list[Report]:
        """Take one event, in id order; return anything newly worth reporting."""
        self.processed_through = max(self.processed_through, event["id"])
        worker, project = event["worker_id"], event["project_id"]
        if event["id"] <= self.decided_through.get(worker, 0):
            return []
        key = (worker, project)
        window = self.windows.setdefault(key, deque(maxlen=WINDOW))
        answer = event.get("answer") or {}
        window.append((event["id"], Answer(
            submission_id=event["submission_id"],
            time_on_task_ms=event["time_on_task_ms"],
            words=event["task_words"],
            pasted=bool((event.get("client_signals") or {}).get("pasted")),
            rationale=answer.get("rationale"),
        )))
        findings = evaluate([a for _, a in reversed(window)])
        if not findings:
            return []
        level = severity(findings)
        before = self.reported.setdefault(key, {})
        # Report a rule the first time it fires, and again if the severity rises to a case.
        new = [f for f in findings if f.rule not in before or (level == "case" and before[f.rule] != "case")]
        for f in findings:
            before[f.rule] = "case" if level == "case" else before.get(f.rule, "watch")
        return [
            Report(worker, event["vendor_id"], project, f.rule, f.version, level, f.observation, f.evidence, event["id"])
            for f in new
        ]
