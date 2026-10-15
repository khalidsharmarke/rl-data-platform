"""M13 done-check: environment briefs and versions (EC0–EC2, EC8, D3, D33, D34, N22, VM6, VM7).

A brief goes only to vendors that supply environments; a builder works only against their
vendor's briefs; a version's files are stored once by hash in three parts and never change;
status moves only along the state machine, with an audit event each time; other vendors see
nothing.
"""

import base64

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.builders import org_ids
from tests.people import BRIGHTLINE_ADMIN, DATA_OPS, KESTREL_ADMIN, NORTHWIND_ADMIN, RESEARCHER

BUILDER = "Sofia Lindgren"  # Kestrel's environment builder

BRIEF = {
    "description": "Bug-fix tasks in small Python libraries, each with hidden tests.",
    "languages": ["python"],
    "constraints": ["Nothing copied from public benchmarks"],
    "difficulty_mix": {"easy": 0.5, "medium": 0.5},
    "review_rubric": ["The task statement is clear", "The tests check the behavior the task asks for"],
}


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def bundle(fix: str = "return s.lower()") -> dict:
    return {
        "test_command": "pytest -q grader",
        "files": [
            {"path": "task/README.md", "part": "task", "content_b64": b64("Make slugify() handle empty strings.")},
            {"path": "task/slug.py", "part": "task", "content_b64": b64("def slugify(s):\n    return s[0].lower()\n")},
            {"path": "grader/test_slug.py", "part": "grader", "content_b64": b64("from slug import slugify\n\ndef test_empty():\n    assert slugify('') == ''\n")},
            {"path": "reference/slug.py", "part": "reference", "content_b64": b64(f"def slugify(s):\n    {fix}\n")},
        ],
    }


async def make_brief(client, login, name="Python bug fixes") -> str:
    r = await client.post("/projects", headers=await login(RESEARCHER), json={
        "name": name, "kind": "environment", "target_volume": 4, "brief": BRIEF})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def allocate_brief(client, login, project_id, vendor="Kestrel Labs", kind="environments", unit="environment"):
    ops = await login(DATA_OPS)
    vendor_id = (await org_ids(client, ops))[vendor]
    contract = await client.post(f"/orgs/{vendor_id}/contracts", headers=ops, json={
        "kind": kind, "price": "120", "price_unit": unit, "effective_from": "2026-01-01"})
    assert contract.status_code == 201, contract.text
    return await client.post(f"/projects/{project_id}/allocations", headers=ops,
                             json={"vendor_id": vendor_id, "contract_id": contract.json()["id"], "share": "1"})


async def kestrel_environment(client, login, name="slugify") -> tuple[str, str, dict]:
    project_id = await make_brief(client, login)
    assert (await allocate_brief(client, login, project_id)).status_code == 201
    builder = await login(BUILDER)
    env = await client.post("/environments", headers=builder, json={"project_id": project_id, "name": name})
    assert env.status_code == 201, env.text
    return project_id, env.json()["id"], builder


async def test_a_brief_is_a_project_of_its_own_kind(client, login):
    researcher = await login(RESEARCHER)
    project_id = await make_brief(client, login)
    p = (await client.get(f"/projects/{project_id}", headers=researcher)).json()
    assert p["kind"] == "environment" and p["brief"]["review_rubric"] == BRIEF["review_rubric"]
    mixed = {"name": "x", "kind": "environment", "target_volume": 1, "brief": BRIEF, "overlap": 2}
    assert (await client.post("/projects", headers=researcher, json=mixed)).status_code == 422
    no_brief = {"name": "x", "kind": "environment", "target_volume": 1}
    assert (await client.post("/projects", headers=researcher, json=no_brief)).status_code == 422
    bad_mix = {**mixed, "overlap": None, "brief": {**BRIEF, "difficulty_mix": {"easy": 0.9}}}
    assert (await client.post("/projects", headers=researcher, json=bad_mix)).status_code == 422
    tasks = await client.post(f"/projects/{project_id}/tasks", headers=researcher, json={"tasks": [{"payload": {}}]})
    assert tasks.status_code == 422


async def test_a_brief_goes_only_to_vendors_that_supply_environments(client, login):
    project_id = await make_brief(client, login)
    northwind = await allocate_brief(client, login, project_id, vendor="Northwind Data", kind="human_work", unit="task")
    assert northwind.status_code == 422 and "environments" in northwind.text
    human_contract = await allocate_brief(client, login, project_id, kind="human_work", unit="task")
    assert human_contract.status_code == 422
    assert (await allocate_brief(client, login, project_id)).status_code == 201


async def test_a_builder_works_only_against_their_vendors_briefs(client, login):
    builder = await login(BUILDER)
    unallocated = await make_brief(client, login, name="Not for Kestrel")
    r = await client.post("/environments", headers=builder, json={"project_id": unallocated, "name": "x"})
    assert r.status_code == 404
    # A builder at a vendor without the environments capability can't submit anything.
    northwind_id = (await org_ids(client, await login(DATA_OPS)))["Northwind Data"]
    added = await client.post(f"/orgs/{northwind_id}/members", headers=await login(NORTHWIND_ADMIN),
                              json={"display_name": "Nora Builder", "role": "environment_builder"})
    assert added.status_code == 201, added.text
    project_id = await make_brief(client, login)
    await allocate_brief(client, login, project_id)
    r = await client.post("/environments", headers=await login("Nora Builder"), json={"project_id": project_id, "name": "x"})
    assert r.status_code == 403


async def test_a_version_stores_its_files_once_by_hash_in_three_parts(client, login, session):
    _, env_id, builder = await kestrel_environment(client, login)
    v1 = (await client.post(f"/environments/{env_id}/versions", headers=builder, json=bundle())).json()
    parts = {f["path"]: f["part"] for f in v1["manifest"]["files"]}
    assert parts == {"task/README.md": "task", "task/slug.py": "task", "grader/test_slug.py": "grader", "reference/slug.py": "reference"}
    assert v1["number"] == 1 and v1["status"] == "draft" and v1["manifest"]["test_command"] == "pytest -q grader"
    before = await session.scalar(text("SELECT count(*) FROM content_objects"))
    v2 = (await client.post(f"/environments/{env_id}/versions", headers=builder, json=bundle(fix="return s.strip().lower()"))).json()
    assert v2["number"] == 2
    # Only the changed reference file is new; the other three are shared with version 1.
    assert await session.scalar(text("SELECT count(*) FROM content_objects")) == before + 1
    hashes = lambda v: {f["path"]: f["hash"] for f in v["manifest"]["files"]}  # noqa: E731
    assert [p for p in hashes(v1) if hashes(v1)[p] != hashes(v2)[p]] == ["reference/slug.py"]
    content = await client.get(f"/versions/{v1['id']}/files/task/slug.py", headers=builder)
    assert content.status_code == 200 and b"def slugify" in content.content


@pytest.mark.parametrize("change", [
    lambda b: {**b, "files": [f for f in b["files"] if f["part"] != "grader"]},
    lambda b: {**b, "files": b["files"] + [{"path": "../escape.py", "part": "task", "content_b64": b64("x")}]},
    lambda b: {**b, "files": b["files"] + [{"path": "task/x.py", "part": "secret", "content_b64": b64("x")}]},
    lambda b: {**b, "files": b["files"] + [{"path": "task/x.py", "part": "task", "content_b64": "not base64!"}]},
    lambda b: {**b, "files": b["files"] + [{"path": "task/answers.py", "part": "reference", "content_b64": b64("x")}]},
])
async def test_a_version_must_be_well_formed(client, login, change):
    _, env_id, builder = await kestrel_environment(client, login)
    assert (await client.post(f"/environments/{env_id}/versions", headers=builder, json=change(bundle()))).status_code == 422


async def database_refuses(session, sql, params) -> bool:
    try:
        async with session.begin_nested():
            await session.execute(text(sql), params)
        return False
    except DBAPIError:
        return True


async def test_a_version_never_changes_even_by_direct_sql(client, login, session):
    _, env_id, builder = await kestrel_environment(client, login)
    v = (await client.post(f"/environments/{env_id}/versions", headers=builder, json=bundle())).json()
    assert await database_refuses(session, "UPDATE environment_versions SET manifest = '{}' WHERE id = :v", {"v": v["id"]})
    assert await database_refuses(session, "UPDATE environment_versions SET number = 9 WHERE id = :v", {"v": v["id"]})
    assert await database_refuses(session, "DELETE FROM content_objects", {})
    # Once a version gets past validation it's kept for good (N22).
    await session.execute(text("UPDATE environment_versions SET status = 'approved' WHERE id = :v"), {"v": v["id"]})
    assert await database_refuses(session, "DELETE FROM environment_versions WHERE id = :v", {"v": v["id"]})
    draft = (await client.post(f"/environments/{env_id}/versions", headers=builder, json=bundle(fix="return ''"))).json()
    assert not await database_refuses(session, "DELETE FROM environment_versions WHERE id = :v", {"v": draft["id"]})


async def test_only_legal_transitions_succeed_and_each_is_audited(client, login):
    _, env_id, builder = await kestrel_environment(client, login)
    v = (await client.post(f"/environments/{env_id}/versions", headers=builder, json=bundle())).json()
    submitted = await client.post(f"/versions/{v['id']}/submit", headers=builder)
    assert submitted.status_code == 200 and submitted.json()["status"] == "submitted"
    again = await client.post(f"/versions/{v['id']}/submit", headers=builder)
    assert again.status_code == 409
    log = (await client.get("/audit?limit=50", headers=await login(KESTREL_ADMIN))).json()
    actions = [e["action"] for e in log if e["entity_id"] == v["id"]]
    assert "environment_version.created" in actions and "environment_version.submitted" in actions


async def test_other_vendors_see_nothing_and_anthropic_sees_everything(client, login):
    project_id, env_id, builder = await kestrel_environment(client, login)
    v = (await client.post(f"/environments/{env_id}/versions", headers=builder, json=bundle())).json()
    brightline = await login(BRIGHTLINE_ADMIN)
    assert (await client.get("/environments", headers=brightline)).json() == []
    for path in (f"/versions/{v['id']}", f"/versions/{v['id']}/files/task/slug.py", f"/environments/{env_id}/versions"):
        assert (await client.get(path, headers=brightline)).status_code == 404
    ops = await login(DATA_OPS)
    assert [e["name"] for e in (await client.get(f"/environments?project_id={project_id}", headers=ops)).json()] == ["slugify"]
    assert (await client.get(f"/versions/{v['id']}/files/reference/slug.py", headers=ops)).status_code == 200
