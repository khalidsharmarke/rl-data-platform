"""M14 done-check, sandbox side: the three checks on real containers (EC3, N8, N21, A11, A12).

Needs the Docker daemon and the base image, so it runs in the sandbox service (`make test`):
each broken fixture is stopped by the check aimed at it, and hostile fixtures can't reach the
network, outrun the time limit, exceed the memory limit or see the grader during the attempt.
"""

import shutil
import subprocess

import pytest

from app import sandbox
from tests import envfixtures

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.skipif(shutil.which("docker") is None, reason="needs the Docker CLI and daemon (the sandbox service)"),
]

IMAGE = "rl-env-python:1"


def check(name: str) -> dict[str, sandbox.CheckResult]:
    files = envfixtures.files(name)
    manifest = {**envfixtures.manifest(name), "base_image": IMAGE,
                "files": [{"path": p, "part": part, "hash": p} for p, part, _ in files]}
    return {r.name: r for r in sandbox.run_checks(manifest, {p: data for p, _, data in files})}


@pytest.mark.parametrize("fixture", ["sound", "sound_punctuation"])
def test_a_sound_environment_passes_all_three_checks(fixture):
    results = check(fixture)
    assert all(r.passed for r in results.values()), {k: r.detail for k, r in results.items()}
    assert results["reference"].outcome == "pass" and results["empty"].outcome == "fail"


def test_a_grader_that_rewards_anything_fails_the_empty_check():
    results = check("passes_anything")
    assert results["reference"].passed and not results["empty"].passed
    assert "empty answer" in results["empty"].detail


def test_an_unsolvable_task_fails_the_reference_check():
    results = check("unsolvable")
    assert not results["reference"].passed and "doesn't pass" in results["reference"].detail


def test_a_flaky_grader_is_caught():
    results = check("flaky")
    # The lucky test fails half the time: either the reference run fails, or the ten repeats
    # disagree (all ten matching, with the reference passing, has odds of about 1 in 2,000).
    assert not (results["reference"].passed and results["repeat"].passed)


def test_there_is_no_network():
    results = check("hostile_network")
    assert not results["reference"].passed
    assert "Network is unreachable" in results["reference"].logs or "OSError" in results["reference"].logs


def test_a_runaway_check_is_stopped_and_cleaned_up(monkeypatch):
    monkeypatch.setattr(sandbox, "GRADE_SECONDS", 8)
    monkeypatch.setattr(sandbox, "REPEAT_SECONDS", 12)
    results = check("hostile_runaway")
    assert results["reference"].outcome == "timeout" and not results["reference"].passed
    leftover = subprocess.run(["docker", "ps", "-q", "--filter", "name=rl-check-"], capture_output=True, text=True).stdout
    assert leftover.strip() == ""


def test_memory_is_capped():
    results = check("hostile_memory")
    assert not results["reference"].passed


def test_the_attempt_never_sees_the_grader_or_the_reference_files():
    results = check("hostile_peek")
    logs = results["empty"].logs.split("== grading ==")[0]
    assert "/work/task/slug.py" in logs  # the attempt sees the task files
    assert "def test_" not in logs and "/work/grader" not in logs.split("grader:")[0]
    assert '"-".join' not in results["empty"].logs  # the reference solution never appears
    assert all(r.passed for r in results.values())


def test_a_weak_grader_passes_every_check():
    # It takes a model attempting the task to find this one: the sandbox can't (M17-M19).
    results = check("weak_grader")
    assert all(r.passed for r in results.values()), {k: r.detail for k, r in results.items()}


@pytest.mark.parametrize("fixture, scores", [("weak_grader", True), ("sound", False), ("sound_punctuation", False)])
def test_returning_an_empty_string_really_beats_the_weak_grader(fixture, scores):
    """The demo's planted hack, run for real: B6's modeled training is grounded in this."""
    files = {p: data for p, part, data in envfixtures.files(fixture) if part in ("task", "grader")}
    files["task/slug.py"] = b'def slugify(text):\n    return ""\n'
    r = sandbox.run(IMAGE, files, sandbox.GRADE, {"TEST_COMMAND": envfixtures.manifest(fixture)["test_command"]}, 30)
    assert (r.exit_code == 0) is scores, r.stdout.decode()
