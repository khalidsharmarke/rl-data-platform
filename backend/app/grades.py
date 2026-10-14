"""Contributor grades (DC11, D30): how far the platform trusts each worker on a project.

Two separate axes, because low quality and abuse need different responses (PRD PD4):

- Quality: gold accuracy with a 95% Wilson interval, read against the project's bar. "Meets"
  when even the low end clears the bar, "below" when even the high end misses it, otherwise
  "uncertain". 90% on 20 answers is uncertain against an 80% bar; 90% on 200 meets it.
- Integrity: a tier.
    suspended a person suspended or removed the worker; nothing of theirs is released
    held      an open case holds the worker's answers
    watch     an open escalation: a watch finding (D32) that later gold hasn't settled yet
    unproven  fewer than PROVEN gold answers: not enough history to trust
    trusted   everything else

The integrity tier sets how many gold answers the release stage needs after an answer
(D29). Grades are computed from the platform's own records, the same way for every vendor
(N16), and never act on a person: they only change how much evidence release asks for.
Workers never see them and vendors don't get them per worker (N17).
"""

import math
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import escalation
from app.models import Project
from app.quality import measures_by

GRADES_VERSION = "grades-v1"
PROVEN = 20  # gold answers before a worker stops being unproven (A35)
LATER_GOLD = {"trusted": 1, "unproven": 3, "watch": 5}  # gold needed after an answer to verify it (A35)
Z = 1.96


def wilson(correct: int, n: int) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = correct / n
    centre = p + Z * Z / (2 * n)
    spread = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))
    denom = 1 + Z * Z / n
    return (centre - spread) / denom, (centre + spread) / denom


def quality_grade(correct: int, n: int, bar: float) -> str:
    interval = wilson(correct, n)
    if interval is None:
        return "uncertain"
    low, high = interval
    return "meets" if low >= bar else "below" if high < bar else "uncertain"


@dataclass
class Grade:
    worker_id: uuid.UUID
    gold_n: int
    gold_correct: int
    interval: tuple[float, float] | None
    quality: str
    integrity: str
    watch_progress: tuple[int, int] | None  # gold answered, and answered right, since the watch finding

    @property
    def later_gold_needed(self) -> int | None:
        return LATER_GOLD.get(self.integrity)

    def snapshot(self) -> dict:
        d = asdict(self)
        d["worker_id"] = str(self.worker_id)
        return {"version": GRADES_VERSION, **{k: d[k] for k in ("gold_n", "gold_correct", "interval", "quality", "integrity")}}


async def compute(session: AsyncSession, project: Project) -> dict[uuid.UUID, Grade]:
    bar = float(project.quality_bar)
    measures = await measures_by(session, project.id, "worker_id")
    held = set((await session.execute(text("SELECT worker_id FROM holds WHERE released_at IS NULL"))).scalars())
    suspended = set((await session.execute(text("SELECT id FROM users WHERE suspended_at IS NOT NULL"))).scalars())
    watches = await escalation.progress(session, project.id)
    grades = {}
    for worker_id, m in measures.items():
        progress = watches.get(worker_id)
        watching = progress is not None
        if worker_id in suspended:
            integrity = "suspended"
        elif worker_id in held:
            integrity = "held"
        elif watching:
            integrity = "watch"
        elif m.gold_n < PROVEN:
            integrity = "unproven"
        else:
            integrity = "trusted"
        grades[worker_id] = Grade(
            worker_id=worker_id, gold_n=m.gold_n, gold_correct=m.gold_correct, interval=wilson(m.gold_correct, m.gold_n),
            quality=quality_grade(m.gold_correct, m.gold_n, bar), integrity=integrity,
            watch_progress=progress if watching else None,
        )
    return grades
