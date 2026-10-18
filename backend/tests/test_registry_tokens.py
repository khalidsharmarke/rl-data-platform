"""M26 done-check, API side: registry credentials are the vendor's API tokens (VM2, D45, D50).

The token endpoint gives a vendor token push and pull on its own namespace and pull on the
base image, nothing else; a token without the registry scope, or a revoked one, can't sign in.
"""

import base64

import jwt
import pytest

from app import registry
from app.config import settings
from tests.people import DATA_OPS
from tests.test_environments import allocate_brief, bundle, make_brief
from tests.test_tokens import bearer, make_token


@pytest.fixture(autouse=True)
def registry_on(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "registry_url", "http://registry.invalid:5000")
    monkeypatch.setattr(settings, "registry_key_dir", str(tmp_path))
    monkeypatch.setattr(settings, "registry_platform_secret", "test-platform-secret")
    registry._signing_key.cache_clear()
    registry.ensure_keys()
    yield
    registry._signing_key.cache_clear()


def basic(password: str, user: str = "ci") -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


async def access(client, password: str, scope: str, user: str = "ci") -> list[dict]:
    r = await client.get("/registry/token", params={"service": "rl-registry", "scope": scope}, headers=basic(password, user))
    assert r.status_code == 200, r.text
    claims = jwt.decode(r.json()["token"], options={"verify_signature": False})
    assert claims["aud"] == settings.registry_service and claims["iss"] == settings.registry_issuer
    return claims["access"]


async def test_a_vendor_token_reaches_only_its_own_namespace_and_the_base_image(client, login):
    token = await make_token(client, login, scopes=["registry"])
    scope = ("repository:vendors/kestrel-labs/runtime:pull,push repository:vendors/northwind-data/runtime:pull,push "
             "repository:platform/base:pull,push repository:platform/images:pull")
    granted = {a["name"]: a["actions"] for a in await access(client, token["secret"], scope)}
    assert granted == {"vendors/kestrel-labs/runtime": ["pull", "push"], "platform/base": ["pull"]}
    # docker login asks for no scope at all.
    assert await access(client, token["secret"], "") == []


async def test_a_token_without_the_scope_or_revoked_cant_sign_in(client, login):
    no_scope = await make_token(client, login, name="api-only", scopes=["environments"])
    r = await client.get("/registry/token", headers=basic(no_scope["secret"]))
    assert r.status_code == 403
    token = await make_token(client, login, name="ci-registry", scopes=["registry"])
    await client.post(f"/tokens/{token['id']}/revoke", headers=await login(DATA_OPS))
    assert (await client.get("/registry/token", headers=basic(token["secret"]))).status_code == 401
    assert (await client.get("/registry/token")).status_code == 401
    assert (await client.get("/registry/token", headers=basic("guess", "platform"))).status_code == 401


async def test_the_platform_gets_what_it_asks_for(client):
    granted = await access(client, "test-platform-secret", "repository:platform/images:pull", user="platform")
    assert granted == [{"type": "repository", "name": "platform/images", "actions": ["pull"]}]


async def test_a_version_names_only_its_own_vendors_image_by_digest(client, login):
    brief = await make_brief(client, login)
    await allocate_brief(client, login, brief)
    token = await make_token(client, login)
    env = (await client.post("/environments", headers=bearer(token), json={"project_id": brief, "name": "slugify"})).json()
    for image in ["vendors/northwind-data/runtime@sha256:" + "0" * 64, "vendors/kestrel-labs/runtime:1"]:
        r = await client.post(f"/environments/{env['id']}/versions", headers=bearer(token), json={**bundle(), "image": image})
        assert r.status_code == 422 and "vendors/kestrel-labs/<name>@sha256" in r.text


async def test_a_registry_token_cant_use_the_api(client, login):
    token = await make_token(client, login, name="registry-only", scopes=["registry"])
    assert (await client.get("/environments", headers=bearer(token))).status_code == 403
