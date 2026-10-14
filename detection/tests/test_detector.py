"""The detection service on recorded events: rules, severity (D32), reporting once, replay,
and judging afresh after a decision (D20)."""

from detector import Detector
from rules import Answer, evaluate, severity

PASTE = "Answer A is more helpful because it directly addresses the user's question with accurate, actionable detail."


def window(seconds, pasted=False, reason=None, words=48):
    return [Answer(str(i), int(seconds * 1000), words, pasted, reason) for i in range(10)]


def test_rules_flag_pasted_duplicated_fast_answers():
    found = {f.rule for f in evaluate(window(3, pasted=True, reason=PASTE))}
    assert found == {"speed", "paste", "duplicate"}


def test_rules_leave_honest_low_quality_alone():
    # Sloppy but honest: quick-ish, typed, short or no reasons. Low quality is not abuse (PD4).
    assert evaluate(window(9)) == []
    assert evaluate(window(30, reason="Clearer steps.")) == []
    assert evaluate(window(3)[:9]) == []  # not enough answers to judge


def test_one_signal_is_a_watch_and_agreeing_signals_are_a_case():
    assert severity(evaluate(window(3))) == "watch"  # speed alone
    assert severity(evaluate(window(30, pasted=True, reason="Clearer steps."))) == "watch"  # paste alone
    assert severity(evaluate(window(3, pasted=True, reason=PASTE))) == "case"


def test_a_client_signal_never_makes_a_case_on_its_own():
    # Pasting is reported by the browser; it can't be the only support for a case (AB11).
    paste_only = [f for f in evaluate(window(30, pasted=True, reason=PASTE)) if f.rule == "paste"]
    assert severity(paste_only) == "watch"


def event(i, worker="w1", seconds=30, pasted=False, reason=None, project="p1"):
    answer = {"choice": "A", **({"rationale": reason} if reason else {})}
    return {"id": i, "submission_id": f"s{i}", "worker_id": worker, "vendor_id": "v1", "project_id": project,
            "time_on_task_ms": seconds * 1000, "task_words": 48, "answer": answer, "client_signals": {"pasted": pasted}}


def feed(d, events):
    return [r for e in events for r in d.process(e)]


def test_reports_each_rule_once_and_again_only_when_it_becomes_a_case():
    d = Detector()
    # Fast but typed: speed alone, a watch finding.
    reports = feed(d, [event(i, seconds=3) for i in range(1, 11)])
    assert [(r.rule, r.severity) for r in reports] == [("speed", "watch")]
    assert feed(d, [event(11, seconds=3)]) == []  # nothing new
    # Then fast, pasted and duplicated: the signals agree, so it's a case.
    reports = feed(d, [event(i, seconds=3, pasted=True, reason=PASTE) for i in range(12, 22)])
    assert {(r.rule, r.severity) for r in reports} == {("speed", "case"), ("paste", "case"), ("duplicate", "case")}
    assert d.processed_through == 21


def test_workers_and_projects_are_judged_separately():
    d = Detector()
    events = [event(i, worker="w1" if i % 2 else "w2", seconds=3) for i in range(1, 21)]
    assert {r.worker_id for r in feed(d, events)} == {"w1", "w2"}
    d2 = Detector()
    mixed = [event(i, project="p1" if i % 2 else "p2", seconds=3) for i in range(1, 11)]
    assert feed(d2, mixed) == []  # five on each project: not enough to judge either


def test_after_a_decision_only_later_answers_count():
    d = Detector()
    feed(d, [event(i, seconds=3) for i in range(1, 11)])
    d.apply_label({"worker_id": "w1", "through_event": 10})
    assert feed(d, [event(i, seconds=3) for i in range(11, 20)]) == []  # nine since the decision
    assert [r.rule for r in feed(d, [event(20, seconds=3)])] == ["speed"]  # ten: judged again


def test_replaying_rebuilds_state_without_repeating_reports():
    events = [event(i, seconds=3) for i in range(1, 15)]
    live = Detector()
    first = feed(live, events)
    restarted = Detector()
    feed(restarted, events)  # what replay does
    assert first and feed(restarted, [event(15, seconds=3)]) == feed(live, [event(15, seconds=3)]) == []


def test_replay_applies_decisions_before_the_events_they_cover():
    # On restart the service loads every label first, then replays events; answers a decision
    # already covered must not count toward the next finding.
    d = Detector()
    d.apply_label({"worker_id": "w1", "through_event": 10})
    assert feed(d, [event(i, seconds=3) for i in range(1, 20)]) == []  # 1-10 covered, 11-19 only nine
    assert [r.rule for r in feed(d, [event(20, seconds=3)])] == ["speed"]
