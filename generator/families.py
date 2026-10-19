"""Task families for the code task generator (EC7, D47, D48).

Each family is a hand-written template for one kind of Python bug: the task statement and
buggy code the model works in, hidden tests that grade a fix, the reference fix, and probes,
known-wrong answers for the shortcuts the family invites. Parameters (names, constants,
examples) vary every task, and the difficulty sets how much the tests demand.

`make(family, difficulty, rng)` returns a task as {path: text}, with each path under task/,
grader/, reference/ or probe/<name>/.
"""

import random
from collections.abc import Callable

TEST_COMMAND = "PYTHONPATH=task python -m pytest -q -p no:cacheprovider grader"
DIFFICULTIES = ("easy", "medium", "hard")
Task = dict[str, str]


def clamp(difficulty: str, rng: random.Random) -> Task:
    fn, module = rng.choice(["clamp", "limit", "bound"]), rng.choice(["clamping", "bounds", "ranges"])  # never a standard-library name
    low, high = rng.randint(-20, 0), rng.randint(5, 40)
    hard = difficulty == "hard"
    readme = (f"`{fn}(value, low, high)` in {module}.py should return value limited to the range low..high.\n"
              f"It returns the wrong result when value is above high: {fn}({high + 7}, {low}, {high}) should be {high}.\n")
    if hard:
        readme += "It must also raise ValueError when low is greater than high.\n"
    tests = [f"def test_above_high():\n    assert {fn}({high + 7}, {low}, {high}) == {high}\n",
             f"def test_inside():\n    assert {fn}({low + 1}, {low}, {high}) == {low + 1}\n"]
    if difficulty != "easy":
        tests += [f"def test_below_low():\n    assert {fn}({low - 3}, {low}, {high}) == {low}\n",
                  f"def test_on_the_edges():\n    assert {fn}({high}, {low}, {high}) == {high} and {fn}({low}, {low}, {high}) == {low}\n"]
    if hard:
        tests += [f"def test_floats():\n    assert {fn}({high + 0.5}, {low}, {high}) == {high}\n",
                  f"def test_bad_range():\n    with pytest.raises(ValueError):\n        {fn}(1, {high}, {low})\n"]
    check = "    if low > high:\n        raise ValueError('low is greater than high')\n" if hard else ""
    return {
        "task/README.md": readme,
        f"task/{module}.py": f"def {fn}(value, low, high):\n    if value < low:\n        return low\n    return value\n",
        f"grader/test_{module}.py": f"import pytest\n\nfrom {module} import {fn}\n\n\n" + "\n\n".join(tests),
        f"reference/{module}.py": f"def {fn}(value, low, high):\n{check}    return max(low, min(value, high))\n",
        f"probe/returns_value/{module}.py": f"def {fn}(value, low, high):\n    return value\n",
        f"probe/returns_high/{module}.py": f"def {fn}(value, low, high):\n    return high\n",
        **({f"probe/no_validation/{module}.py": f"def {fn}(value, low, high):\n    return max(low, min(value, high))\n"} if hard else {}),
    }


def dedupe(difficulty: str, rng: random.Random) -> Task:
    fn, module = rng.choice(["dedupe", "unique", "distinct"]), rng.choice(["lists", "seqs", "collections_util"])
    words = rng.sample(["pear", "fig", "kiwi", "lime", "plum", "date", "sloe", "yuzu"], 5)
    items = [words[0], words[1], words[0], words[2], words[1], words[3]]
    expected = [words[0], words[1], words[2], words[3]]
    hard = difficulty == "hard"
    readme = (f"`{fn}(items)` in {module}.py should drop repeated items, keeping the first of each in its original order.\n"
              f"It loses the order: {fn}({items!r}) should be {expected!r}.\n")
    if hard:
        readme += f"It must also take an optional `key` function: two items are repeats when their keys are equal.\n"
    tests = [f"def test_keeps_order():\n    assert {fn}({items!r}) == {expected!r}\n"]
    if difficulty != "easy":
        tests += [f"def test_empty():\n    assert {fn}([]) == []\n",
                  f"def test_all_same():\n    assert {fn}([{words[4]!r}] * 4) == [{words[4]!r}]\n"]
    if hard:
        tests += [f"def test_key():\n    assert {fn}(['Ab', 'ab', 'B', 'b'], key=str.lower) == ['Ab', 'B']\n"]
    signature = "items, key=None" if hard else "items"
    body = ("    seen, out = set(), []\n    for item in items:\n        k = key(item) if key else item\n"
            "        if k not in seen:\n            seen.add(k)\n            out.append(item)\n    return out\n") if hard else (
            "    seen, out = set(), []\n    for item in items:\n        if item not in seen:\n            seen.add(item)\n"
            "            out.append(item)\n    return out\n")
    return {
        "task/README.md": readme,
        f"task/{module}.py": f"def {fn}({signature}):\n    return list(set(items))\n",
        f"grader/test_{module}.py": f"from {module} import {fn}\n\n\n" + "\n\n".join(tests),
        f"reference/{module}.py": f"def {fn}({signature}):\n{body}",
        f"probe/unchanged/{module}.py": f"def {fn}({signature}):\n    return list(items)\n",
        f"probe/sorted_set/{module}.py": f"def {fn}({signature}):\n    return sorted(set(items))\n",
    }


def parse_duration(difficulty: str, rng: random.Random) -> Task:
    # Planted template bug, for the demo (like the weak_grader fixture): whatever the difficulty, the
    # tests only check the example in the README, so a function that hard-codes it scores full marks.
    # The `hardcodes_example` probe catches it in the sandbox, and the family is stopped.
    fn, module = rng.choice(["parse_duration", "to_seconds", "duration_seconds"]), rng.choice(["timeparse", "durations"])
    h, m = rng.randint(1, 9), rng.randint(1, 59)
    example, seconds = f"{h}h{m}m", h * 3600 + m * 60
    readme = (f"`{fn}(text)` in {module}.py turns a duration such as '{example}' into seconds.\n"
              f"It ignores the minutes: {fn}('{example}') should be {seconds}.\n")
    reference = (f"import re\n\n\ndef {fn}(text):\n    total = 0\n"
                 f"    for amount, unit in re.findall(r'(\\d+)\\s*([hms])', text):\n"
                 f"        total += int(amount) * {{'h': 3600, 'm': 60, 's': 1}}[unit]\n    return total\n")
    return {
        "task/README.md": readme,
        f"task/{module}.py": f"import re\n\n\ndef {fn}(text):\n    hours = re.search(r'(\\d+)h', text)\n"
                             f"    return int(hours.group(1)) * 3600 if hours else 0\n",
        f"grader/test_{module}.py": f"from {module} import {fn}\n\n\ndef test_example():\n    assert {fn}('{example}') == {seconds}\n",
        f"reference/{module}.py": reference,
        f"probe/returns_zero/{module}.py": f"def {fn}(text):\n    return 0\n",
        f"probe/hardcodes_example/{module}.py": f"def {fn}(text):\n    return {seconds}\n",
    }


FAMILIES: dict[str, Callable[[str, random.Random], Task]] = {"clamp": clamp, "dedupe": dedupe, "parse_duration": parse_duration}
# Families whose template is known to be broken, so its tests expect the probes check to fail.
PLANTED_BUGS = {"parse_duration"}


def make(family: str, difficulty: str, rng: random.Random) -> Task:
    return FAMILIES[family](difficulty, rng)
