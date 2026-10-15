"""Training statistics (TO2, TO4, EC3, D39): computed by the platform from episodes, the same
way for every vendor, and independent of any detector.

The processor is incremental: it folds in only episodes after the last sequence number it
processed, and moves that mark in the same transaction as the sums. Running it twice, or
retrying it after a crash, can't count an episode twice (D6), and its work grows with what's
new, not with the whole history. Episodes are grouped into buckets of training steps, so a
run's statistics can be followed over the run.
"""

import math
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.config import settings

BATCH = 50_000


async def process(session: AsyncSession) -> int:
    """Fold new episodes into training_stats. Returns how many were folded in."""
    # The row lock makes concurrent processors take turns.
    through = await session.scalar(text("SELECT through_seq FROM training_stats_progress WHERE id = 1 FOR UPDATE"))
    upto = await session.scalar(
        text("SELECT max(seq) FROM (SELECT seq FROM episodes WHERE seq > :t ORDER BY seq LIMIT :n) x"), {"t": through, "n": BATCH}
    )
    if upto is None:
        await session.rollback()
        return 0
    result = await session.execute(
        text(
            """
            INSERT INTO training_stats (run_id, version_id, step_bucket, episodes, passed, reward_sum, reward_sq_sum, length_sum)
            SELECT run_id, version_id, step / :b, count(*), count(*) FILTER (WHERE passed), sum(reward), sum(reward * reward), sum(length)
            FROM episodes WHERE seq > :t AND seq <= :u
            GROUP BY run_id, version_id, step / :b
            ON CONFLICT (run_id, version_id, step_bucket) DO UPDATE SET
                episodes = training_stats.episodes + EXCLUDED.episodes,
                passed = training_stats.passed + EXCLUDED.passed,
                reward_sum = training_stats.reward_sum + EXCLUDED.reward_sum,
                reward_sq_sum = training_stats.reward_sq_sum + EXCLUDED.reward_sq_sum,
                length_sum = training_stats.length_sum + EXCLUDED.length_sum
            """
        ),
        {"b": settings.training_step_bucket, "t": through, "u": upto},
    )
    folded = await session.scalar(text("SELECT count(*) FROM episodes WHERE seq > :t AND seq <= :u"), {"t": through, "u": upto})
    await session.execute(
        text("UPDATE training_stats_progress SET through_seq = :u, updated_at = :now WHERE id = 1"),
        {"u": upto, "now": clock.now()},
    )
    await session.commit()
    return folded


@dataclass
class Stats:
    episodes: int
    passed: int
    reward_sum: float
    reward_sq_sum: float
    length_sum: int

    @property
    def solve_rate(self) -> float | None:
        return self.passed / self.episodes if self.episodes else None

    @property
    def mean_reward(self) -> float | None:
        return self.reward_sum / self.episodes if self.episodes else None

    @property
    def reward_variance(self) -> float | None:
        if not self.episodes:
            return None
        mean = self.reward_sum / self.episodes
        return max(0.0, self.reward_sq_sum / self.episodes - mean * mean)

    @property
    def mean_length(self) -> float | None:
        return self.length_sum / self.episodes if self.episodes else None

    @property
    def difficulty(self) -> str | None:
        """A band from the solve rate (EC3): what training found, not what the builder intended."""
        rate = self.solve_rate
        if rate is None:
            return None
        if rate >= 0.95:
            return "too easy"
        if rate >= 0.7:
            return "easy"
        if rate >= 0.3:
            return "medium"
        return "hard" if rate >= 0.05 else "too hard"

    def as_dict(self) -> dict:
        return {"episodes": self.episodes, "solve_rate": self.solve_rate, "mean_reward": self.mean_reward,
                "reward_variance": self.reward_variance, "mean_length": self.mean_length, "difficulty": self.difficulty}


def finite(x: float | None) -> float | None:
    return None if x is None or math.isnan(x) else x
