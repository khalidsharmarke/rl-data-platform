"""M28 done-check, platform side: probe solutions and generated versions (EC7, D47, D48).

Probes are a fourth part, stored apart like the reference and never offered to training; a
generated version records its family, difficulty and batch; the generator signs in with its
own token and submits through the public API like a vendor.
"""

import base64

from app.config import settings
from app.routes.catalog import entry
from app.seed import seed
from tests.test_environments import allocate_brief, bundle, make_brief

PROBE = {"path": "probe/returns_empty/slug.py", "part": "probe", "content_b64": base64.b64encode(b"def slugify(s):\n    return ''\n").decode()}
GENERATED = {"family": "clamp", "difficulty": "medium", "batch": "batch-1"}


async def test_a_generated_version_with_probes_from_the_generators_token(client, login, session, monkeypatch):
    monkeypatch.setattr(settings, "generator_token", "rlp1_test-generator-token")
    await seed(session)
    generator = {"Authorization": "Bearer rlp1_test-generator-token"}
    brief = await make_brief(client, login)
    assert (await allocate_brief(client, login, brief, vendor="Code task generator")).status_code == 201
    env = (await client.post("/environments", headers=generator, json={"project_id": brief, "name": "clamp-medium-1234abcd"})).json()
    r = await client.post(f"/environments/{env['id']}/versions", headers=generator,
                          json={**bundle(), "files": [*bundle()["files"], PROBE], "generated": GENERATED})
    assert r.status_code == 201, r.text
    manifest = r.json()["manifest"]
    assert manifest["generated"] == GENERATED
    assert [f["part"] for f in manifest["files"]].count("probe") == 1
    # Training gets the task files and the grader, never the reference or the probes.
    from types import SimpleNamespace
    offered = entry(SimpleNamespace(id=r.json()["id"], number=1, manifest=manifest), SimpleNamespace(id=env["id"], name=env["name"], project_id=brief))
    assert {f.part for f in offered.files} == {"task", "grader"}


async def test_probe_files_need_a_name_and_a_version_needs_no_probes(client, login):
    brief = await make_brief(client, login)
    await allocate_brief(client, login, brief)
    builder = await login("Sofia Lindgren")
    env = (await client.post("/environments", headers=builder, json={"project_id": brief, "name": "x"})).json()
    nameless = {**PROBE, "path": "probe/slug.py"}
    r = await client.post(f"/environments/{env['id']}/versions", headers=builder, json={**bundle(), "files": [*bundle()["files"], nameless]})
    assert r.status_code == 422 and "probe/<probe name>/" in r.text
    assert (await client.post(f"/environments/{env['id']}/versions", headers=builder, json=bundle())).status_code == 201
