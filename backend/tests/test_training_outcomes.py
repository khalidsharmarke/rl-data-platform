"""M20 done-check, platform side: training outcomes on the vendor scorecard (TO7, VP3, N16)."""

from app import training_stats
from tests.people import BRIGHTLINE_ADMIN, DATA_OPS, KESTREL_ADMIN
from tests.test_hack_recall import finding, report_findings, train, weak_approved


async def test_training_outcomes_are_attributed_to_the_vendor(client, login, session):
    # 40 episodes, the last 20 hacked and passing; the detector raises a watch, then a case.
    project_id, version_id = await weak_approved(client, login, session)
    await train(client, version_id)
    await training_stats.process(session)
    watch = finding(version_id, severity="watch")
    watch["evidence"] = {**watch["evidence"], "flagged_episodes": 8}
    await report_findings(client, watch, finding(version_id))
    [row] = (await client.get(f"/vendors/environment-work?project_id={project_id}", headers=await login(DATA_OPS))).json()
    assert row["vendor_name"] == "Kestrel Labs"
    assert (row["episodes"], row["solved"], row["flagged"], row["recalls"]) == (40, 20, 20, 1)  # running totals aren't added up
    assert row["solve_rate"] == 0.5 and row["flagged_share"] == 0.5
    # The vendor sees its own outcomes; another vendor sees nothing of them.
    assert [r["recalls"] for r in (await client.get("/vendors/environment-work", headers=await login(KESTREL_ADMIN))).json()] == [1]
    assert (await client.get("/vendors/environment-work", headers=await login(BRIGHTLINE_ADMIN))).json() == []


async def test_a_vendor_whose_versions_training_never_used_shows_no_outcomes(client, login, session):
    project_id, _ = await weak_approved(client, login, session)
    [row] = (await client.get(f"/vendors/environment-work?project_id={project_id}", headers=await login(DATA_OPS))).json()
    assert (row["episodes"], row["flagged"], row["recalls"], row["solve_rate"], row["flagged_share"]) == (0, 0, 0, None, None)
