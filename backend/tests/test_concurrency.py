"""Concurrent claims never exceed a task's overlap (D2, N7).

Runs against its own database with real commits, because the race only appears between
separate transactions. Many workers at one vendor drain a project at the same time.
"""

import asyncio
import collections

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import demo_content
from app.config import settings
from app.db import get_session
from app.main import app
from app.seed import seed
from tests.conftest import fresh_database
from tests.people import DATA_OPS, NORTHWIND_ADMIN, RESEARCHER

WORKERS = 12
TASKS = 30
OVERLAP = 2


@pytest.fixture(scope="module")
def concurrency_database_url() -> str:
    return fresh_database("rl_test_concurrency")


async def test_concurrent_claims_respect_overlap(concurrency_database_url, monkeypatch):
    engine = create_async_engine(concurrency_database_url, pool_size=WORKERS + 2)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as s:
        await seed(s)

    async def real_session():
        async with sessions() as s:
            yield s

    monkeypatch.setattr(settings, "demo_logins", True)
    app.dependency_overrides[get_session] = real_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            users = (await c.get("/auth/demo-users")).json()

            async def as_person(name):
                user = next(u for u in users if u["display_name"] == name)
                token = (await c.post("/auth/demo-login", json={"user_id": user["id"]})).json()["access_token"]
                return {"Authorization": f"Bearer {token}"}

            admin = await as_person(NORTHWIND_ADMIN)
            northwind = next(u for u in users if u["display_name"] == NORTHWIND_ADMIN)["org_id"]
            for i in range(WORKERS):
                await c.post(f"/orgs/{northwind}/members", headers=admin, json={"display_name": f"Parallel {i}", "role": "worker"})
            users = (await c.get("/auth/demo-users")).json()
            workers = [await as_person(u["display_name"]) for u in users if u["display_name"].startswith("Parallel ")]

            researcher, ops = await as_person(RESEARCHER), await as_person(DATA_OPS)
            project = (await c.post("/projects", headers=researcher, json={
                "name": "Concurrency", "task_type": demo_content.TASK_TYPE, "answer_schema": demo_content.ANSWER_SCHEMA,
                "instructions": "x", "target_volume": TASKS * OVERLAP, "overlap": OVERLAP, "gold_rate": "0"})).json()
            tasks = [{"payload": g.payload} for g in demo_content.generate(TASKS)]
            await c.post(f"/projects/{project['id']}/tasks", headers=researcher, json={"tasks": tasks})
            contract = (await c.post(f"/orgs/{northwind}/contracts", headers=ops, json={
                "kind": "human_work", "price": "1", "price_unit": "task", "effective_from": "2026-01-01"})).json()
            await c.post(f"/projects/{project['id']}/allocations", headers=ops,
                         json={"vendor_id": northwind, "contract_id": contract["id"], "share": "1"})

            async def drain(headers):
                claimed = []
                while (r := await c.post("/assignments/next", headers=headers, json={"project_id": project["id"]})).status_code == 200:
                    claimed.append(r.json()["task"]["id"])
                return claimed

            results = await asyncio.gather(*(drain(h) for h in workers))
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

    per_task = collections.Counter(t for claimed in results for t in claimed)
    assert max(per_task.values()) <= OVERLAP
    assert sum(per_task.values()) == TASKS * OVERLAP
    assert all(len(c) == len(set(c)) for c in results)
