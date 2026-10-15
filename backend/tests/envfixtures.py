"""Load a fixture environment from tests/fixtures/environments/<name>/."""

import base64
import json
from pathlib import Path

ROOT = Path(__file__).parent / "fixtures" / "environments"


def files(name: str) -> list[tuple[str, str, bytes]]:
    base = ROOT / name
    return [(str(p.relative_to(base)), p.relative_to(base).parts[0], p.read_bytes())
            for p in sorted(base.rglob("*")) if p.is_file() and p.name != "manifest.json"]


def manifest(name: str) -> dict:
    return json.loads((ROOT / name / "manifest.json").read_text())


def version_body(name: str) -> dict:
    """The body for POST /environments/{id}/versions."""
    return {**manifest(name), "files": [
        {"path": path, "part": part, "content_b64": base64.b64encode(data).decode()} for path, part, data in files(name)]}
