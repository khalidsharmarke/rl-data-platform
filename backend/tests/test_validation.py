"""M14 done-check, platform side: the validation queue and what results do to a version
(EC3, EC4, D2, D3, D4, N10). The checks themselves run in containers: test_sandbox_runs.py.
"""

from types import SimpleNamespace

from sqlalchemy import text

from app import sandbox
from app.jobs import run_one
from app.sandbox import CheckResult
from tests import envfixtures
from tests.builders import org_ids
from tests.people import BRIGHTLINE_ADMIN, DATA_OPS, KESTREL_ADMIN
from tests.test_environments import BUILDER, allocate_brief, make_brief


async def submitted(client, login, builder_name=BUILDER, vendor="Kestrel Labs", project_id=None, name="sound", fixture="sound"):
    if project_id is None:
        project_id = await make_brief(client, login)
        assert (await allocate_brief(client, login, project_id, vendor=vendor)).status_code == 201
    builder = await login(builder_name)
    env = (await client.post("/environments", headers=builder, json={"project_id": project_id, "name": name})).json()
    v = (await client.post(f"/environments/{env['id']}/versions", headers=builder, json=envfixtures.version_body(fixture))).json()
    assert (await client.post(f"/versions/{v['id']}/submit", headers=builder)).status_code == 200
    return project_id, v["id"], builder


def fake(results: dict[str, bool]):
    def checks(manifest, contents):
        assert {f["part"] for f in manifest["files"]} == {"task", "grader", "reference"} and contents
        return [CheckResult(name, ok, "pass" if ok else "fail", f"{name} {'ok' if ok else 'broke'}", 5, f"logs of {name}")
                for name, ok in results.items()]
    return checks


async def test_submitting_queues_a_validation_job_the_general_worker_leaves_alone(client, login, session):
    _, version_id, _ = await submitted(client, login)
    job = (await session.execute(text("SELECT kind, status, payload FROM jobs WHERE dedupe_key = :k"),
                                 {"k": f"validate_version:{version_id}"})).one()
    assert job.kind == "validate_version" and job.status == "queued" and job.payload["version_id"] == version_id
    while await run_one(session):
        pass
    assert await session.scalar(text("SELECT status FROM jobs WHERE dedupe_key = :k"), {"k": f"validate_version:{version_id}"}) == "queued"


async def test_results_move_the_version_and_are_kept_with_their_logs(client, login, session):
    _, good, builder = await submitted(client, login)
    _, bad, _ = await submitted(client, login, name="broken", fixture="passes_anything")
    while (job := await sandbox.claim(session)) is not None:
        ok = job.payload["version_id"] == good
        await sandbox.process(session, job, checks=fake({"reference": True, "empty": ok, "repeat": True}))
    assert (await client.get(f"/versions/{good}", headers=builder)).json()["status"] == "in_review"
    assert (await client.get(f"/versions/{bad}", headers=builder)).json()["status"] == "failed"
    checks = (await client.get(f"/versions/{bad}/checks", headers=builder)).json()
    assert [(c["check_name"], c["passed"]) for c in checks] == [("reference", True), ("empty", False), ("repeat", True)]
    assert checks[1]["logs"] == "logs of empty" and checks[1]["detail"] == "empty broke"
    log = (await client.get("/audit?limit=100", headers=await login(KESTREL_ADMIN))).json()
    moves = [e["after"]["status"] for e in reversed(log) if e["entity_id"] == bad and e["action"].startswith("environment_version.")
             and e["after"] and "status" in e["after"]]
    assert moves == ["submitted", "validating", "failed"]
    assert (await client.get(f"/versions/{bad}/checks", headers=await login(BRIGHTLINE_ADMIN))).status_code == 404


async def test_one_vendors_burst_doesnt_hold_back_another(client, login, session):
    # Brightline takes on environment work too, with its own builder.
    ops = await login(DATA_OPS)
    brightline = (await org_ids(client, ops))["Brightline Annotation"]
    await client.put(f"/orgs/{brightline}/capabilities", headers=ops, json={"capabilities": ["human_work", "environments"]})
    await client.post(f"/orgs/{brightline}/members", headers=await login(BRIGHTLINE_ADMIN),
                      json={"display_name": "Bea Builder", "role": "environment_builder"})
    project_id, first, _ = await submitted(client, login, name="k1")
    kestrel = [first] + [(await submitted(client, login, project_id=project_id, name=f"k{i}"))[1] for i in range(2, 5)]
    assert (await allocate_brief(client, login, project_id, vendor="Brightline Annotation")).status_code == 201
    _, late, _ = await submitted(client, login, builder_name="Bea Builder", project_id=project_id, name="b1")
    order = []
    while (job := await sandbox.claim(session)) is not None:
        order.append(job.payload["version_id"])
        await session.execute(text("UPDATE jobs SET status = 'done' WHERE id = :id"), {"id": job.id})
    # Brightline's one submission, queued after Kestrel's four, is taken second, not fifth.
    assert order == [kestrel[0], late, *kestrel[1:]]


async def test_a_running_check_counts_against_its_vendor(client, login, session):
    project_id, first, _ = await submitted(client, login, name="k1")
    _, second, _ = await submitted(client, login, project_id=project_id, name="k2")
    job = await sandbox.claim(session)
    assert job.payload["version_id"] == first
    # With Kestrel's first check still running, its next job waits behind nobody else's,
    # but it is still taken when it's the only one.
    assert (await sandbox.claim(session)).payload["version_id"] == second
    assert await sandbox.claim(session) is None
