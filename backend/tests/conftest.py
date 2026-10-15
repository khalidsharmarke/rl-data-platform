import asyncio
from collections.abc import AsyncIterator

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import get_session
from app.main import app
from app.seed import seed

TEST_DB = "rl_test"


async def recreate_database(url, name: str) -> None:
    conn = await asyncpg.connect(
        user=url.username, password=url.password, host=url.host, port=url.port, database="postgres"
    )
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {name}")
    finally:
        await conn.close()


def fresh_database(name: str) -> str:
    """Create (or recreate) a database, migrate it to head, and return its URL. Call outside an event loop."""
    url = make_url(settings.database_url)
    asyncio.run(recreate_database(url, name))
    db_url = url.set(database=name).render_as_string(hide_password=False)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(config, "head")
    return db_url


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """A fresh, fully migrated database for the whole test run."""
    return fresh_database(TEST_DB)


@pytest.fixture
async def session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back after each test, including commits made by the app."""
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
        await seed(session)
        yield session
        await session.close()
        await outer.rollback()
    await engine.dispose()


@pytest.fixture
async def client(session: AsyncSession, monkeypatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(settings, "demo_logins", True)
    monkeypatch.setattr(settings, "detection_token", "test-detection-token")
    monkeypatch.setattr(settings, "training_token", "test-training-token")

    async def override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def login(client):
    """Sign in as a seeded person by name; returns request headers."""

    async def as_person(display_name: str) -> dict[str, str]:
        users = (await client.get("/auth/demo-users")).json()
        user = next(u for u in users if u["display_name"] == display_name)
        token = (await client.post("/auth/demo-login", json={"user_id": user["id"]})).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return as_person
