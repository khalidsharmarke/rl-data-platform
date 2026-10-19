"""Task types (DC1, D53): everything that differs from one kind of human-data task to another.

Each type names its answer schema (the default when a researcher doesn't give one, and the fields
any schema for it must require), how a gold answer is checked, and how two workers' answers are
compared for agreement. Routing, release, scorecards and exports never look at the type; the
measures in quality.py take these expressions, so adding a type is one entry here plus how the
work screen shows it.

Expressions are SQL over `{a}`, the answer being judged, and `g.answer` (the gold answer) or
`o.answer` (another worker's answer on the same task).
"""

from dataclasses import dataclass

from app import demo_content

OUTCOMES = ["succeeded", "partly", "failed"]


@dataclass(frozen=True)
class TaskType:
    name: str
    label: str
    schema: dict
    requires: tuple[str, ...]  # fields the gold and agreement expressions read
    gold: str | None  # None: no automatic gold; quality comes from reviewers' spot checks
    agrees: str | None  # None: agreement isn't measured


TYPES = {t.name: t for t in [
    TaskType(
        name=demo_content.TASK_TYPE, label="Compare two answers", schema=demo_content.ANSWER_SCHEMA, requires=("choice",),
        gold="{a} @> g.answer", agrees="{a}->'choice' = o.answer->'choice'",
    ),
    TaskType(
        name="rating", label="Rate an answer from 1 to 5",
        schema={"type": "object", "properties": {"score": {"type": "integer", "minimum": 1, "maximum": 5},
                                                  "rationale": {"type": "string", "maxLength": 2000}},
                "required": ["score"], "additionalProperties": False},
        requires=("score",),
        # Within one point counts: people differ on 3 or 4, not on 1 or 5.
        gold="abs(({a}->>'score')::int - (g.answer->>'score')::int) <= 1",
        agrees="abs(({a}->>'score')::int - (o.answer->>'score')::int) <= 1",
    ),
    TaskType(
        name="writing", label="Write an answer",
        schema={"type": "object", "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 8000}},
                "required": ["text"], "additionalProperties": False},
        requires=("text",), gold=None, agrees=None,
    ),
    TaskType(
        name="trajectory_review", label="Review an agent's transcript",
        schema={"type": "object", "properties": {
            # The first step that goes wrong, counting from 1, or 0 if none does (D54).
            "first_wrong_step": {"type": "integer", "minimum": 0},
            "outcome": {"enum": OUTCOMES},
            "rationale": {"type": "string", "maxLength": 2000}},
            "required": ["first_wrong_step", "outcome"], "additionalProperties": False},
        requires=("first_wrong_step", "outcome"),
        gold="({a}->'first_wrong_step' = g.answer->'first_wrong_step' AND {a}->'outcome' = g.answer->'outcome')",
        agrees="({a}->'first_wrong_step' = o.answer->'first_wrong_step' AND {a}->'outcome' = o.answer->'outcome')",
    ),
]}


def case(field: str, answer: str, task_type: str) -> str:
    """A SQL CASE on a task type column choosing each type's expression for `field` ("gold" or "agrees")."""
    whens = [f"WHEN '{t.name}' THEN {getattr(t, field).format(a=answer)}" for t in TYPES.values() if getattr(t, field)]
    return f"(CASE {task_type} {' '.join(whens)} ELSE NULL END)"


WITHOUT_AGREEMENT = ", ".join(f"'{t.name}'" for t in TYPES.values() if t.agrees is None) or "''"
WITHOUT_GOLD = {t.name for t in TYPES.values() if t.gold is None}
