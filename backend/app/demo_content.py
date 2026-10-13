"""Synthetic "which answer is more helpful?" tasks for the demo and the simulator.

Each pair has one clearly better answer. The generator is deterministic for a given seed,
so the simulator can know the better answer without the platform storing it anywhere
except for gold tasks.
"""

import random
from dataclasses import dataclass

TASK_TYPE = "pairwise_preference"

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "choice": {"enum": ["A", "B"]},
        "rationale": {"type": "string", "maxLength": 2000},
    },
    "required": ["choice"],
    "additionalProperties": False,
}

INSTRUCTIONS = (
    "Read the question and both answers. Choose the answer that is more helpful: correct, "
    "complete enough to act on, and clear. Add a one-line reason if it helps."
)

# (question, better answer, worse answer)
PAIRS = [
    ("How do I reverse a list in Python?",
     "Use my_list.reverse() to reverse it in place, or my_list[::-1] to get a reversed copy.",
     "Python has many list methods; check the documentation."),
    ("What's the difference between a process and a thread?",
     "A process has its own memory space; threads run inside a process and share its memory, which makes them cheaper to create but needs care with shared data.",
     "Threads are faster than processes."),
    ("How long should I boil an egg for a soft yolk?",
     "About 6 to 7 minutes in boiling water, then move it to cold water to stop the cooking.",
     "Boil it until it's done."),
    ("Why is the sky blue?",
     "Air molecules scatter short blue wavelengths of sunlight much more than red ones (Rayleigh scattering), so blue light reaches your eyes from every direction.",
     "Because it reflects the ocean."),
    ("How do I undo my last git commit but keep the changes?",
     "Run git reset --soft HEAD~1. The commit is removed and its changes stay staged.",
     "Delete the .git folder and start again."),
    ("What does HTTP status 404 mean?",
     "The server couldn't find the requested resource; the URL may be wrong or the item was removed.",
     "It means the server crashed."),
    ("How can I make a SQL query faster?",
     "Check the query plan with EXPLAIN, add an index on the columns you filter or join on, and select only the columns you need.",
     "Buy a faster computer."),
    ("What is a good way to start learning to run?",
     "Alternate one minute of running with two of walking for 20 to 30 minutes, three times a week, and build up the running time gradually.",
     "Run as far as you can every day."),
    ("How do I convert Celsius to Fahrenheit?",
     "Multiply by 9/5 and add 32. For example, 20 °C is 20 × 9/5 + 32 = 68 °F.",
     "Add 30 to the Celsius number."),
    ("What should I include in a bug report?",
     "Steps to reproduce, what you expected, what happened instead, the version or environment, and any error messages or screenshots.",
     "Say that it's broken."),
    ("Why do we need database transactions?",
     "They make a group of changes all-or-nothing, so a failure halfway can't leave data inconsistent, and they isolate concurrent changes from each other.",
     "Transactions make databases faster."),
    ("How do I keep basil fresh?",
     "Trim the stems and keep it in a glass of water at room temperature, loosely covered; the fridge turns basil black.",
     "Put it in the freezer."),
]


@dataclass(frozen=True)
class GeneratedTask:
    payload: dict
    better: str  # "A" or "B"


def generate(n: int, seed: int = 7) -> list[GeneratedTask]:
    rng = random.Random(seed)
    tasks = []
    for i in range(n):
        question, good, bad = PAIRS[i % len(PAIRS)]
        better = rng.choice("AB")
        a, b = (good, bad) if better == "A" else (bad, good)
        tasks.append(GeneratedTask(payload={"prompt": question, "response_a": a, "response_b": b, "item": i}, better=better))
    return tasks
