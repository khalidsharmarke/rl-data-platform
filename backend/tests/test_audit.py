"""The audit log is append-only at the database level (VM4)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_events SET action = 'tampered'", "DELETE FROM audit_events", "TRUNCATE audit_events"],
)
async def test_audit_history_cannot_be_changed_or_removed(session, statement):
    assert (await session.scalar(text("SELECT count(*) FROM audit_events"))) > 0
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(text(statement))


async def test_every_seeded_write_has_an_audit_event(session):
    orgs = await session.scalar(text("SELECT count(*) FROM orgs"))
    users = await session.scalar(text("SELECT count(*) FROM users"))
    created = await session.scalar(text("SELECT count(*) FROM audit_events WHERE action IN ('org.created', 'user.added')"))
    assert created == orgs + users
