"""M22 done-check: broken-task flags and disagreement alerts (DC5, D43)."""

from sqlalchemy import text

from app.routes import task_health
from app.scorecards import significant_drop
from tests.builders import allocate, make_project
from tests.people import DATA_OPS, KESTREL_WORKER, NORTHWIND_WORKER, NORTHWIND_WORKER_2, RESEARCHER

BROKEN = "Both answers are word for word the same, so neither can be more helpful."


async def claim(client, headers, project_id):
    r = await client.post("/assignments/next", headers=headers, json={"project_id": project_id})
    return r.json() if r.status_code == 200 else None


async def flag(client, headers, task_id):
    return await client.post(f"/tasks/{task_id}/flag", headers=headers, json={"reason": BROKEN})


async def gold_first_project(client, login):
    """Every task is gold, so every task is routed to both vendors, in the same order."""
    project_id, generated = await make_project(client, login, tasks=6, gold_every=1)
    await allocate(client, login, project_id, "Northwind Data", "0.5")
    await allocate(client, login, project_id, "Kestrel Labs", "0.5")
    return project_id, generated


async def health(client, login, project_id):
    return (await client.get(f"/projects/{project_id}/task-health", headers=await login(RESEARCHER))).json()


async def test_two_flags_pause_a_task_and_nobody_is_given_it_again(client, login, session):
    project_id, _ = await gold_first_project(client, login)
    first = await claim(client, await login(NORTHWIND_WORKER), project_id)
    assert (await flag(client, await login(NORTHWIND_WORKER), first["task"]["id"])).status_code == 201
    assert (await health(client, login, project_id))["broken"] == []  # one flag: still routed
    second = await claim(client, await login(NORTHWIND_WORKER_2), project_id)
    assert second["task"]["id"] == first["task"]["id"]
    assert (await flag(client, await login(NORTHWIND_WORKER_2), second["task"]["id"])).json() == {}  # says nothing more
    [broken] = (await health(client, login, project_id))["broken"]
    assert broken["broken_status"] == "paused" and broken["is_gold"] and len(broken["flags"]) == 2
    kestrel = await claim(client, await login(KESTREL_WORKER), project_id)
    assert kestrel["task"]["id"] != first["task"]["id"]  # gold is flagged and paused like any other task


async def test_a_flag_records_no_answer_for_the_flagger(client, login, session):
    project_id, _ = await gold_first_project(client, login)
    worker = await login(NORTHWIND_WORKER)
    task = await claim(client, worker, project_id)
    await flag(client, worker, task["task"]["id"])
    assert await session.scalar(text("SELECT count(*) FROM submissions WHERE project_id = :p"), {"p": project_id}) == 0
    assert (await claim(client, worker, project_id))["task"]["id"] != task["task"]["id"]  # on to the next task
    assert (await flag(client, worker, task["task"]["id"])).status_code == 404  # only a task you have open


async def test_researchers_restore_or_retire_and_a_retired_task_counts_nowhere(client, login, session):
    project_id, generated = await gold_first_project(client, login)
    kestrel = await login(KESTREL_WORKER)
    gold = await claim(client, kestrel, project_id)
    await client.post(f"/assignments/{gold['id']}/submit", headers=kestrel, json={"answer": {"choice": "A"}})
    for name in (NORTHWIND_WORKER, NORTHWIND_WORKER_2):
        headers = await login(name)
        await flag(client, headers, (await claim(client, headers, project_id))["task"]["id"])
    task_id = gold["task"]["id"]
    decide = lambda who, d: client.post(f"/tasks/{task_id}/broken-decision", headers=who,  # noqa: E731
                                        json={"decision": d, "reason": "Checked: the two answers are identical."})
    assert (await decide(await login(DATA_OPS), "retire")).status_code == 403
    quality = lambda: client.get(f"/projects/{project_id}/quality", headers=await_login_ops)  # noqa: E731
    await_login_ops = await login(DATA_OPS)
    before = {v["label"]: v for v in (await quality()).json()["vendors"]}
    assert before["Kestrel Labs"]["submissions"] == 1 and before["Kestrel Labs"]["gold_n"] == 1
    assert (await decide(await login(RESEARCHER), "retire")).json() == {"broken_status": "retired"}
    after = {v["label"]: v for v in (await quality()).json()["vendors"]}
    assert after.get("Kestrel Labs", {"submissions": 0})["submissions"] == 0  # not in volume, gold or acceptance
    assert (await decide(await login(RESEARCHER), "restore")).status_code == 409  # only a paused task
    flaggers = (await health(client, login, project_id))["flaggers"]
    assert sorted((f["flags"], f["retired"]) for f in flaggers) == [(1, 1), (1, 1)]


async def test_a_restored_task_is_routed_again_and_needs_new_flags(client, login, session):
    project_id, _ = await gold_first_project(client, login)
    for name in (NORTHWIND_WORKER, NORTHWIND_WORKER_2):
        headers = await login(name)
        task_id = (await claim(client, headers, project_id))["task"]["id"]
        await flag(client, headers, task_id)
    r = await client.post(f"/tasks/{task_id}/broken-decision", headers=await login(RESEARCHER),
                          json={"decision": "restore", "reason": "The answers differ; the second one cites a source."})
    assert r.json() == {"broken_status": None}
    kestrel = await login(KESTREL_WORKER)
    again = await claim(client, kestrel, project_id)
    assert again["task"]["id"] == task_id  # back in the stream
    await flag(client, kestrel, task_id)  # one new flag: the two old ones no longer count
    assert (await health(client, login, project_id))["broken"] == []


async def test_a_drop_in_agreement_raises_one_alert_with_the_worst_tasks(client, login, session, monkeypatch):
    monkeypatch.setattr(task_health, "RECENT", 20)
    monkeypatch.setattr(task_health, "MIN_SIDE", 20)
    project_id, generated = await make_project(client, login, tasks=60, overlap=2)
    await allocate(client, login, project_id, "Northwind Data", "1")
    assert (await health(client, login, project_id))["agreement_alert"] is None
    flipped = set()
    for name, flip_from in ((NORTHWIND_WORKER, None), (NORTHWIND_WORKER_2, 40)):
        headers = await login(name)
        n = 0
        while (a := await claim(client, headers, project_id)) is not None:
            item = a["task"]["payload"]["item"]
            better = next(g.better for g in generated if g.payload["item"] == item)
            flip = flip_from is not None and n >= flip_from  # the second worker's last 20 answers disagree
            flipped |= {item} if flip else set()
            await client.post(f"/assignments/{a['id']}/submit", headers=headers,
                              json={"answer": {"choice": ("B" if better == "A" else "A") if flip else better}})
            n += 1
    alert = (await health(client, login, project_id))["agreement_alert"]
    assert alert["recent"] == 0 and alert["baseline"] == 0.8 and len(alert["tasks"]) == 5
    assert {t["payload"]["item"] for t in alert["tasks"]} <= flipped
    assert (await client.get(f"/projects/{project_id}/task-health", headers=await login(NORTHWIND_WORKER))).status_code == 403


def test_the_alert_needs_more_than_noise_because_it_is_checked_again_and_again():
    # 300 recent answers at 67% against 700 earlier at 76%: z is just under 3. Noise like this turns up
    # somewhere in a long run when the window is checked every few minutes; it isn't an alert.
    assert not significant_drop(201, 300, 532, 700, drop=task_health.DROP, z=task_health.Z)
    assert significant_drop(192, 300, 525, 700, drop=task_health.DROP, z=task_health.Z)  # 64% against 75%: z 3.4
