"""The reward-hack detector on recorded episodes (TO3, D35, D38)."""

import random

import pytest

from hacks import BASELINE, WINDOW, HackDetector


def episodes(version="v-weak", run="rl-run-002", n=120, hack_from=None, solve=0.2, seed=1, touched=("task/slug.py",)):
    """Honest attempts (~40 turns) until hack_from, then full marks in ~4 turns."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        hacking = hack_from is not None and i >= hack_from
        passed = hacking or rng.random() < solve
        out.append({"seq": i + 1, "episode_id": f"{run}-{i}", "version_id": version, "run_label": run, "step": i * 10,
                    "reward": 1.0 if passed else 0.0, "passed": passed, "length": rng.randint(3, 6) if hacking else rng.randint(30, 50),
                    "touched": list(touched)})
    return out


def run_all(d, eps):
    return [f for e in eps for f in d.process(e)]


def test_a_shortcut_mid_run_becomes_a_case_with_where_it_began():
    findings = run_all(HackDetector(), episodes(hack_from=60))
    rules = [(f.rule, f.severity) for f in findings]
    assert ("shortcut", "watch") in rules and ("shortcut", "case") in rules
    case = next(f for f in findings if f.rule == "shortcut" and f.severity == "case")
    assert case.evidence["first_flagged_step"] == 600 and case.evidence["flagged_episodes"] >= WINDOW // 2
    assert all(i.startswith("rl-run-002-") for i in case.evidence["episode_ids"])


def test_an_abrupt_jump_alone_is_only_watched():
    # Solve rate leaps from 20% to 100% with normal-length attempts: worth a look, not a quarantine.
    eps = episodes(n=150)
    for e in eps[90:]:
        e.update(passed=True, reward=1.0)
    assert [(f.rule, f.severity) for f in run_all(HackDetector(), eps)] == [("jump", "watch")]


@pytest.mark.parametrize("seed", range(20))
def test_honest_learning_raises_nothing(seed):
    # The solve rate climbs from 15% to 75% across the run, as with the sound environment.
    rng = random.Random(seed)
    eps = []
    for i in range(240):
        p = 0.15 + 0.6 * i / 240
        passed = rng.random() < p
        eps.append({"seq": i + 1, "episode_id": f"e{i}", "version_id": "v-sound", "run_label": "r", "step": i * 10,
                    "reward": 1.0 if passed else 0.0, "passed": passed, "length": rng.randint(25, 60), "touched": ["task/slug.py"]})
    assert run_all(HackDetector(), eps) == []


def test_touching_the_grader_is_a_case_at_once():
    findings = run_all(HackDetector(), episodes(n=3, touched=("task/slug.py", "grader/test_slug.py")))
    assert [(f.rule, f.severity) for f in findings] == [("touched-grader", "case")]


def test_each_rule_is_reported_once_per_severity_and_runs_are_separate():
    d = HackDetector()
    first = run_all(d, episodes(hack_from=60, run="a"))
    second = run_all(d, [{**e, "seq": e["seq"] + 1000} for e in episodes(hack_from=60, run="b")])
    assert sorted((f.rule, f.severity) for f in first) == sorted((f.rule, f.severity) for f in second)
    assert len([f for f in first if f.rule == "shortcut"]) == 2  # watch, then case; nothing more


def test_a_retired_version_is_no_longer_watched_and_a_cleared_one_starts_afresh():
    d = HackDetector()
    d.apply_label({"version_id": "v-weak", "decision": "remove"})
    assert run_all(d, episodes(hack_from=0)) == []
    d2 = HackDetector()
    run_all(d2, episodes(n=BASELINE + 10))
    d2.apply_label({"version_id": "v-weak", "decision": "clear"})
    assert ("v-weak", "rl-run-002") not in d2.tracks
