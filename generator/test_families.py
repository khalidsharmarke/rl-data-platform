"""Every family's tasks behave as the sandbox will demand: the reference passes its tests, the
untouched task fails them, and every probe fails them, except for the planted template bug."""

import random
import subprocess
import sys
from pathlib import Path

import pytest

import families


def passes(task: families.Task, overlay: str | None, tmp: Path) -> bool:
    """Run the grader on the task files with an overlay (reference/ or probe/<name>/) applied."""
    for path, text in task.items():
        if path.startswith(("task/", "grader/")):
            (tmp / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp / path).write_text(text)
    for path, text in task.items():
        if overlay and path.startswith(overlay):
            (tmp / "task" / path.removeprefix(overlay)).write_text(text)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "grader"], cwd=tmp,
                       env={"PYTHONPATH": "task"}, capture_output=True)
    return r.returncode == 0


@pytest.mark.parametrize("family", families.FAMILIES)
@pytest.mark.parametrize("difficulty", families.DIFFICULTIES)
@pytest.mark.parametrize("seed", [1, 2])
def test_a_generated_task_is_solvable_and_closes_its_shortcuts(family, difficulty, seed, tmp_path):
    task = families.make(family, difficulty, random.Random(seed))
    assert passes(task, "reference/", tmp_path / "reference")
    assert not passes(task, None, tmp_path / "empty")
    probes = {p.split("/")[1] for p in task if p.startswith("probe/")}
    assert probes, "every generated task carries probes (D47)"
    caught = {name: not passes(task, f"probe/{name}/", tmp_path / name) for name in probes}
    if family in families.PLANTED_BUGS:
        assert not all(caught.values())
    else:
        assert all(caught.values()), caught


def test_parameters_vary_the_content():
    made = {str(sorted(families.make("clamp", "easy", random.Random(s)).items())) for s in range(10)}
    assert len(made) > 5
