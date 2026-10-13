"""The platform's clock.

Normally real time. For the scripted demo only, a demo clock runs faster than real time so a
day of simulated work fits into minutes; every timestamp the platform writes or compares
goes through now(), so durations stay consistent. The anchor lives in the demo_clock table,
so every process agrees on the time. Tokens (auth.py) always use real time.
"""

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

_anchor: tuple[datetime, datetime, int] | None = None  # (real_epoch, sim_epoch, speed)


def now() -> datetime:
    real = datetime.now(UTC)
    if _anchor is None:
        return real
    real_epoch, sim_epoch, speed = _anchor
    return sim_epoch + (real - real_epoch) * speed


def speed() -> int:
    return _anchor[2] if _anchor else 1


async def start(session: AsyncSession) -> None:
    """Load the anchor, creating it if the demo clock is on and none exists yet."""
    global _anchor
    if settings.demo_clock_speed <= 1:
        _anchor = None
        return
    row = (await session.execute(text("SELECT real_epoch, sim_epoch, speed FROM demo_clock WHERE id = 1"))).first()
    if row is None:
        real = datetime.now(UTC)
        await session.execute(
            text("INSERT INTO demo_clock (id, real_epoch, sim_epoch, speed) VALUES (1, :r, :s, :v) ON CONFLICT (id) DO NOTHING"),
            {"r": real, "s": real, "v": settings.demo_clock_speed},
        )
        await session.commit()
        row = (await session.execute(text("SELECT real_epoch, sim_epoch, speed FROM demo_clock WHERE id = 1"))).first()
    _anchor = (row.real_epoch, row.sim_epoch, row.speed)
