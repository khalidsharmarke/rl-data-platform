"""The demo clock runs at a fixed multiple of real time; off, it is real time."""

from datetime import UTC, datetime, timedelta

from app import clock


def test_clock_is_real_time_by_default():
    assert clock._anchor is None
    assert abs(clock.now() - datetime.now(UTC)) < timedelta(seconds=1)


def test_demo_clock_advances_speed_times_faster(monkeypatch):
    real_epoch = datetime.now(UTC) - timedelta(seconds=10)
    sim_epoch = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(clock, "_anchor", (real_epoch, sim_epoch, 360))
    elapsed = clock.now() - sim_epoch
    # 10 real seconds at 360x is an hour of simulated time.
    assert timedelta(minutes=59) < elapsed < timedelta(minutes=61)
    assert clock.speed() == 360
