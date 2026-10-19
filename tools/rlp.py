#!/usr/bin/env python3
"""rlp: a vendor's command-line client for the platform, with nothing but the standard library.

Signs in with one of your vendor's API tokens (RLP_TOKEN) and talks to the API (RLP_API,
default http://localhost:8000). The demo's vendor CI imports these same functions.

    rlp.py onboarding [--wait]                         your onboarding checklist; --wait polls until it's complete
    rlp.py submit DIR [--brief NAME] [--name ENV] [--image REF]
                                                       upload an environment version, submit it, wait for the checks

DIR holds task/, grader/ and reference/ and a manifest.json with "test_command" (and optionally
"prepare_command"). REF is the image you pushed, by tag or digest: localhost:5000/vendors/<you>/<name>:<tag>.
A tag is resolved to its digest first: a version is always pinned to exact image content.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SAMPLE_BRIEF = "Onboarding: sample environment"
MANIFEST_TYPES = ("application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, "
                  "application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json")


class Api:
    def __init__(self, url: str, token: str):
        self.url, self.token = url.rstrip("/"), token

    def call(self, method: str, path: str, body: dict | None = None):
        request = urllib.request.Request(self.url + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as e:
            detail = json.loads(e.read() or b"{}").get("detail", e.reason)
            raise SystemExit(f"{method} {path}: {e.code} {detail}") from None


def pin(api: Api, ref: str) -> str:
    """vendors/<you>/<name>@sha256:..., from a reference by tag or digest, asking the registry for a tag's digest."""
    host, _, path = ref.partition("/")
    if host == "vendors":  # no registry host given
        host, path = "localhost:5000", ref
    if "@" in path:
        return path
    repo, _, tag = path.rpartition(":")
    basic = base64.b64encode(f"rlp:{api.token}".encode()).decode()
    token_url = f"{api.url}/registry/token?service=rl-registry&scope=repository:{repo}:pull"
    with urllib.request.urlopen(urllib.request.Request(token_url, headers={"Authorization": f"Basic {basic}"}), timeout=30) as r:
        registry_token = json.loads(r.read())["token"]
    head = urllib.request.Request(f"http://{host}/v2/{repo}/manifests/{tag}", method="HEAD",
                                  headers={"Authorization": f"Bearer {registry_token}", "Accept": MANIFEST_TYPES})
    with urllib.request.urlopen(head, timeout=30) as r:
        return f"{repo}@{r.headers['Docker-Content-Digest']}"


def checklist(api: Api) -> dict:
    org = api.call("GET", "/orgs")[0]
    return api.call("GET", f"/orgs/{org['id']}/onboarding")


def show(state: dict) -> None:
    print(f"{state['org_name']}: {'complete' if state['complete'] else 'in progress'}")
    for item in state["items"]:
        print(f"  [{'x' if item['done'] else ' '}] {item['label']}: {item['detail']}")


def bundle(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    files = [{"path": str(p.relative_to(directory)), "part": p.relative_to(directory).parts[0],
              "content_b64": base64.b64encode(p.read_bytes()).decode()}
             for p in sorted(directory.rglob("*")) if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts]
    return {**manifest, "files": files}


def submit(api: Api, source: Path | dict, brief: str = SAMPLE_BRIEF, name: str = "sample", image: str | None = None,
           wait_seconds: float = 2) -> tuple[dict, list[dict]]:
    """Create the environment if it's new, upload a version, submit it and wait for the sandbox. Returns (version, checks).

    `source` is a directory, or a version body already built (files, test_command and any other fields)."""
    project = next((p for p in api.call("GET", "/projects") if brief in (p["name"], p["id"])), None)
    if project is None:
        raise SystemExit(f"No brief {brief!r} is allocated to your vendor")
    env = next((e for e in api.call("GET", f"/environments?project_id={project['id']}") if e["name"] == name), None)
    env = env or api.call("POST", "/environments", {"project_id": project["id"], "name": name})
    body = bundle(source) if isinstance(source, Path) else dict(source)
    if image:
        body["image"] = pin(api, image)
    version = api.call("POST", f"/environments/{env['id']}/versions", body)
    api.call("POST", f"/versions/{version['id']}/submit")
    while (version := api.call("GET", f"/versions/{version['id']}"))["status"] in ("submitted", "validating"):
        time.sleep(wait_seconds)
    return version, api.call("GET", f"/versions/{version['id']}/checks")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    onboarding = commands.add_parser("onboarding", help="show your onboarding checklist")
    onboarding.add_argument("--wait", action="store_true", help="poll until the checklist is complete")
    sub = commands.add_parser("submit", help="submit an environment version and wait for the checks")
    sub.add_argument("directory", type=Path)
    sub.add_argument("--brief", default=SAMPLE_BRIEF, help="the brief's name or id (default: the onboarding sample brief)")
    sub.add_argument("--name", default="sample", help="the environment's name; a new version if it exists")
    sub.add_argument("--image", help="your runtime image by digest")
    args = parser.parse_args()
    if not os.environ.get("RLP_TOKEN"):
        raise SystemExit("Set RLP_TOKEN to one of your vendor's API tokens")
    api = Api(os.environ.get("RLP_API", "http://localhost:8000"), os.environ["RLP_TOKEN"])

    if args.command == "onboarding":
        state = checklist(api)
        while args.wait and not state["complete"]:
            time.sleep(5)
            state = checklist(api)
        show(state)
    else:
        version, checks = submit(api, args.directory, args.brief, args.name, args.image)
        print(f"Version {version['number']}: {version['status'].replace('_', ' ')}")
        for c in checks:
            print(f"  [{'x' if c['passed'] else ' '}] {c['check_name']}: {c['detail']}")
        sys.exit(0 if version["status"] in ("in_review", "approved") else 1)


if __name__ == "__main__":
    main()
