"""M21 done-check, platform side: the audit sample, abuse judgements on spot checks, shadow
mode and promotion, and labels back to detection (AB15, AB16, D27, D28, D40, D41)."""

import pytest
from sqlalchemy import text

from app.config import settings
from tests.builders import DETECTION, allocate, answer_like, make_project, report
from tests.people import DATA_OPS, NORTHWIND_ADMIN, NORTHWIND_WORKER, NORTHWIND_WORKER_2, RESEARCHER


@pytest.fixture(autouse=True)
def no_random_audits(monkeypatch):
    """Requests come only from what each test does; the audit test sets its own rate."""
    monkeypatch.setattr(settings, "audit_rate", 0.0)


async def terse_worker(client, login, n=24):
    """A worker whose reasons are all short: honest, and exactly what terse-v1 flags."""
    project_id, _ = await make_project(client, login, tasks=60)
    await allocate(client, login, project_id, "Northwind Data", "1")
    ids = await answer_like(client, await login(NORTHWIND_WORKER), project_id, n=n, pasted=False, reason="Clearer steps.")
    return project_id, ids


async def judge(client, login, submission_id, abusive: bool):
    r = await client.post(f"/submissions/{submission_id}/review", headers=await login(RESEARCHER),
                          json={"verdict": "accepted", "looks_abusive": abusive})
    assert r.status_code == 201, r.text


async def measures(client, login):
    return (await client.get("/detection-quality", headers=await login(DATA_OPS))).json()


def rule(m, version):
    return next(r for r in m["rules"] if r["rule_version"] == version)


async def test_a_new_rule_version_is_in_shadow_and_its_findings_lead_to_nothing(client, login, session):
    project_id, ids = await terse_worker(client, login)
    # Even at case severity, a shadow finding opens no case, holds nothing and starts no escalation.
    await report(client, project_id, NORTHWIND_WORKER, severity="case", rules=("terse",), submission_ids=ids)
    assert (await client.get("/cases", headers=await login(DATA_OPS))).json() == []
    assert await session.scalar(text("SELECT count(*) FROM holds")) == 0
    await report(client, project_id, NORTHWIND_WORKER, severity="watch", rules=("terse",), submission_ids=ids[:10])
    assert await session.scalar(text("SELECT count(*) FROM escalations")) == 0
    assert await session.scalar(text("SELECT bool_and(shadow) FROM findings")) is True
    modes = (await client.get("/detection/rules", headers=DETECTION)).json()["modes"]
    assert modes["terse-v1"] == "shadow" and modes["paste-v1"] == "live"
    # Its flagged answers go to reviewers, a few per finding, so its precision can be measured.
    requested = await session.scalar(text("SELECT count(*) FROM review_requests WHERE reason = 'finding'"))
    assert requested == 6


async def test_live_findings_still_act(client, login, session):
    project_id, ids = await terse_worker(client, login, n=12)
    await report(client, project_id, NORTHWIND_WORKER, severity="case", rules=("speed", "paste"), submission_ids=ids)
    assert len((await client.get("/cases", headers=await login(NORTHWIND_ADMIN))).json()) == 1
    assert await session.scalar(text("SELECT bool_or(shadow) FROM findings")) is False


async def test_spot_checks_put_requested_answers_first_and_look_the_same(client, login, session):
    project_id, ids = await terse_worker(client, login, n=30)
    await report(client, project_id, NORTHWIND_WORKER, severity="watch", rules=("terse",), submission_ids=ids[:10])
    requested = {str(r) for r in await session.scalars(text("SELECT submission_id FROM review_requests"))}
    queue = (await client.get(f"/projects/{project_id}/review-queue?n=5", headers=await login(RESEARCHER))).json()
    assert requested <= {i["submission_id"] for i in queue}  # the 3 requested, plus 2 at random
    assert {frozenset(i) for i in queue} == {frozenset(queue[0])}  # nothing marks an item as requested
    no_judgement = await client.post(f"/submissions/{queue[0]['submission_id']}/review", headers=await login(RESEARCHER),
                                     json={"verdict": "accepted"})
    assert no_judgement.status_code == 422  # asked on every spot check (D40)


async def test_the_audit_sample_measures_what_live_rules_miss(client, login, session, monkeypatch):
    monkeypatch.setattr(settings, "audit_rate", 1.0)
    project_id, _ = await make_project(client, login, tasks=20, gold_every=4)
    await allocate(client, login, project_id, "Northwind Data", "1")
    ids = await answer_like(client, await login(NORTHWIND_WORKER), project_id, n=20, pasted=False, reason="Fine.")
    audited = await session.scalar(text("SELECT count(*) FROM review_requests WHERE reason = 'audit'"))
    assert audited == 15  # every regular answer at this rate; gold is never audited
    regular = [str(s) for s in await session.scalars(text("SELECT submission_id FROM review_requests"))]
    for i, sid in enumerate(regular[:4]):
        await judge(client, login, sid, abusive=i == 0)
    m = await measures(client, login)
    assert (m["audited"], m["audited_abusive"], m["miss_rate"]) == (4, 1, 0.25)
    # Once a live rule has flagged the worker, their audited answers no longer count as misses.
    await report(client, project_id, NORTHWIND_WORKER, severity="watch", rules=("paste",), submission_ids=ids[:1])
    assert (await measures(client, login))["audited"] == 0


async def test_promotion_needs_enough_reviews_and_precision_at_the_bar(client, login, session):
    project_id, ids = await terse_worker(client, login, n=24)
    await report(client, project_id, NORTHWIND_WORKER, severity="watch", rules=("terse",), submission_ids=ids)
    ops = await login(DATA_OPS)
    promote = lambda bar=0.8: client.put("/detectors/terse-v1/mode", headers=ops,  # noqa: E731
                                          json={"mode": "live", "bar": bar, "reason": "Reviewed enough of its flags."})
    for sid in ids[:19]:
        await judge(client, login, sid, abusive=True)
    assert (await promote()).status_code == 409  # 19 reviewed: not enough yet
    await judge(client, login, ids[19], abusive=False)
    r = rule(await measures(client, login), "terse-v1")
    assert (r["reviewed"], r["abusive"], r["precision"], r["can_promote"]) == (20, 19, 0.95, True)
    assert not rule(await measures(client, login), "paste-v1")["can_promote"]  # live already
    assert (await promote(bar=0.96)).status_code == 409  # below the bar
    assert (await client.put("/detectors/terse-v1/mode", headers=await login(RESEARCHER),
                             json={"mode": "live", "bar": 0.8, "reason": "x" * 5})).status_code == 403
    live = await promote()
    assert live.status_code == 200 and live.json()["mode"] == "live" and live.json()["bar"] == 0.8
    event = (await session.execute(text("SELECT org_id, after FROM audit_events WHERE action = 'rule.live'"))).one()
    assert event.org_id is None and event.after["precision"] == 0.95  # Anthropic's log only (N17)
    # Now it acts like any live rule, and the detector is told.
    assert (await client.get("/detection/rules", headers=DETECTION)).json()["modes"]["terse-v1"] == "live"
    await report(client, project_id, NORTHWIND_WORKER_2, severity="case", rules=("terse", "speed"))
    assert len((await client.get("/cases", headers=ops)).json()) == 1
    # And it can be sent back to shadow at any time.
    back = await client.put("/detectors/terse-v1/mode", headers=ops, json={"mode": "shadow", "reason": "Too many honest workers."})
    assert back.status_code == 200 and back.json()["mode"] == "shadow"


async def test_judgements_go_back_to_detection_and_stay_inside_anthropic(client, login, session):
    project_id, ids = await terse_worker(client, login, n=10)
    await judge(client, login, ids[0], abusive=True)
    [o] = (await client.get("/detection/outcomes", headers=DETECTION)).json()["outcomes"]
    assert (o["kind"], o["outcome"], o["submission_id"]) == ("judgement", "abusive", ids[0])
    for who in (NORTHWIND_ADMIN, NORTHWIND_WORKER):
        assert (await client.get("/detection-quality", headers=await login(who))).status_code == 403
    vendor_log = (await client.get("/audit", headers=await login(NORTHWIND_ADMIN))).text
    assert "looks_abusive" not in vendor_log and "abusive" not in vendor_log
    assert (await client.get("/detection/outcomes")).status_code == 401
