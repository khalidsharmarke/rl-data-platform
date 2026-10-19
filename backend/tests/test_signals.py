"""M31 done-check: client and API signals, the claim limit, the vendor grades view, and crashed
validation jobs (AB11, AB12, DC11, D26, D51)."""


from sqlalchemy import text

from app import sandbox
from app.config import settings
from tests.builders import allocate, make_project, org_ids, report
from tests.people import BRIGHTLINE_ADMIN, DATA_OPS, NORTHWIND_ADMIN, NORTHWIND_WORKER, NORTHWIND_WORKER_2
from tests.test_escalation import watched
from tests.test_validation import submitted

SIGNALS = {"pasted": False, "first_input_ms": 850, "focus_changes": 3, "content_viewed": True}


async def project(client, login, tasks=20):
    project_id, generated = await make_project(client, login, tasks=tasks, gold_every=2)
    await allocate(client, login, project_id, "Northwind Data", "1")
    return project_id, {g.payload["item"]: g.better for g in generated}


async def answer(client, headers, project_id, truth, signals=SIGNALS, device=None):
    extra = {"X-Device-Id": device} if device else {}
    a = (await client.post("/assignments/next", headers={**headers, **extra}, json={"project_id": project_id})).json()
    r = await client.post(f"/assignments/{a['id']}/submit", headers={**headers, **extra},
                          json={"answer": {"choice": truth[a["task"]["payload"]["item"]]}, "client_signals": signals})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_client_and_api_signals_are_recorded_and_shown_as_case_evidence(client, login):
    project_id, truth = await project(client, login)
    first, second = await login(NORTHWIND_WORKER), await login(NORTHWIND_WORKER)  # two sign-ins
    ids = [await answer(client, first, project_id, truth, device="laptop"), await answer(client, second, project_id, truth, device="laptop")]
    await answer(client, await login(NORTHWIND_WORKER_2), project_id, truth, device="laptop")  # a colleague on the same browser
    bad = await client.post("/assignments/next", headers=first, json={"project_id": project_id})
    r = await client.post(f"/assignments/{bad.json()['id']}/submit", headers=first,
                          json={"answer": {"choice": "A"}, "client_signals": {"focus_changes": -1}})
    assert r.status_code == 422

    await report(client, project_id, NORTHWIND_WORKER, submission_ids=ids)
    case = next(c for c in (await client.get("/cases", headers=await login(NORTHWIND_ADMIN))).json())
    detail = (await client.get(f"/cases/{case['id']}", headers=await login(NORTHWIND_ADMIN))).json()
    assert detail["samples"][0]["client_signals"] == SIGNALS
    assert detail["api_signals"] == {"claims_last_hour": 3, "most_claims_in_a_minute": 3, "sessions_last_day": 2,
                                     "devices_last_day": 1, "other_workers_on_those_devices": 1}


async def test_claims_are_limited_the_same_for_everyone_and_submissions_never_are(client, login, monkeypatch):
    monkeypatch.setattr(settings, "claims_per_minute", 2)
    project_id, truth = await project(client, login)
    worker = await login(NORTHWIND_WORKER)
    held = (await client.post("/assignments/next", headers=worker, json={"project_id": project_id})).json()
    await client.post("/assignments/next", headers=worker, json={"project_id": project_id})
    over = await client.post("/assignments/next", headers=worker, json={"project_id": project_id})
    assert over.status_code == 429 and "wait a moment" in over.text
    r = await client.post(f"/assignments/{held['id']}/submit", headers=worker, json={"answer": {"choice": truth[held["task"]["payload"]["item"]]}})
    assert r.status_code == 201
    assert (await client.post("/assignments/next", headers=await login(NORTHWIND_WORKER_2), json={"project_id": project_id})).status_code == 200


async def test_a_vendor_sees_only_counts_for_its_own_workers_with_watches_hidden(client, login, session):
    project_id, _generated, _worker = await watched(client, login, session)
    ids = await org_ids(client, await login(DATA_OPS))
    url = f"/vendors/{ids['Northwind Data']}/grades?project_id={project_id}"
    internal = (await client.get(url, headers=await login(DATA_OPS))).json()
    assert sum(internal["quality"].values()) == 1 and sum(internal["integrity"].values()) == 1
    mine = (await client.get(url, headers=await login(NORTHWIND_ADMIN))).json()
    assert set(mine["integrity"]) == {"suspended", "held", "unproven", "trusted"}  # no "watch" (D26)
    # Answers since the last gold refresh don't count for the vendor yet (PD2).
    assert sum(mine["quality"].values()) == 0 and mine["gold_shown_until"]
    assert (await client.get(url, headers=await login(BRIGHTLINE_ADMIN))).status_code == 404


async def test_a_crashed_validation_job_is_retried_then_fails_its_version(client, login, session):
    _, version_id, builder = await submitted(client, login)

    def crash(manifest, contents):
        raise RuntimeError("docker daemon went away")

    outcomes = []
    while (job := await sandbox.claim(session)) is not None:
        outcomes.append(await sandbox.handle(session, job, checks=crash))
    assert outcomes == ["retrying", "retrying", "failed"]
    version = (await client.get(f"/versions/{version_id}", headers=builder)).json()
    assert version["status"] == "failed"
    note = await session.scalar(text("SELECT after->>'note' FROM audit_events WHERE entity_id = :v AND action = 'environment_version.failed'"),
                                {"v": version_id})
    assert "couldn't run, after 3 attempts" in note and "docker daemon went away" in note
