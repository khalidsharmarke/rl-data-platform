"""Environment versions: content stored by hash, three parts kept apart, and the state machine
(EC1, EC2, EC8, D3, D34, N21, N22).

A version is a manifest pointing at files stored once by their SHA-256 in platform-owned
storage. Each file belongs to one part:

- task       what the model works in: the task statement and the code it changes
- grader     the tests that score an attempt; never shown to the model (N21)
- reference  the builder's known-good solution; used only to validate the task (N21)
- probe      optional known-wrong answers, each in probe/<name>/; every one must fail the grader (D47)

The manifest never changes (a database trigger enforces it). Only the status moves, and only
along the state machine below, with an audit event for every move.
"""

import difflib
import hashlib
import uuid
from pathlib import Path, PurePosixPath

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock
from app.audit import record
from app.config import settings
from app.models import Environment, EnvironmentVersion, User

PARTS = ("task", "grader", "reference", "probe")
REQUIRED_PARTS = PARTS[:3]
MAX_FILE_BYTES = 256 * 1024
MAX_VERSION_BYTES = 2 * 1024 * 1024

# D3. "failed" covers both failed checks and a reviewer sending the version back; a quarantined
# version goes back to review (TO6). A fix is always a new version.
TRANSITIONS: dict[str, set[str]] = {
    "draft": {"submitted"},
    "submitted": {"validating"},
    "validating": {"failed", "in_review"},
    "in_review": {"approved", "failed"},
    "approved": {"quarantined"},
    "quarantined": {"in_review"},
    "failed": set(),
}


def content_path(digest: str) -> Path:
    return Path(settings.content_dir) / digest[:2] / digest


async def store(session: AsyncSession, data: bytes) -> str:
    """Store bytes once under their SHA-256 and return the hash. Storing the same bytes again is a no-op."""
    digest = hashlib.sha256(data).hexdigest()
    path = content_path(digest)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    await session.execute(
        text("INSERT INTO content_objects (hash, size, stored_at) VALUES (:h, :s, :now) ON CONFLICT (hash) DO NOTHING"),
        {"h": digest, "s": len(data), "now": clock.now()},
    )
    return digest


def read(digest: str) -> bytes:
    return content_path(digest).read_bytes()


def text_diff(old_hash: str, new_hash: str, path: str, limit: int = 400) -> list[str] | None:
    """A unified diff of two stored files, or None if either isn't text."""
    try:
        old, new = read(old_hash).decode(), read(new_hash).decode()
    except UnicodeDecodeError:
        return None
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", lineterm=""))
    return lines[:limit]


def clean_path(path: str) -> str:
    p = PurePosixPath(path)
    if p.is_absolute() or not p.parts or any(part in ("..", ".", "") for part in p.parts):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Bad file path: {path!r}")
    return str(p)


async def build_manifest(session: AsyncSession, files: list[tuple[str, str, bytes]], test_command: str,
                         prepare_command: str | None, base_image: str) -> dict:
    """files: (path, part, content). Stores each file and returns the version's manifest.

    Each part lives in its own directory: task/, grader/ and reference/. A reference file
    replaces the task file at the same path under task/ when the sandbox applies the solution.
    """
    seen, total, entries = set(), 0, []
    for path, part, data in files:
        path = clean_path(path)
        if part not in PARTS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{path}: part must be one of {', '.join(PARTS)}")
        if not path.startswith(f"{part}/"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{path}: {part} files belong under {part}/")
        if part == "probe" and len(PurePosixPath(path).parts) < 3:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{path}: probe files belong under probe/<probe name>/")
        if path in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{path} appears twice")
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{path} is larger than {MAX_FILE_BYTES // 1024} KB")
        seen.add(path)
        total += len(data)
        entries.append({"path": path, "part": part, "hash": await store(session, data), "size": len(data)})
    if total > MAX_VERSION_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A version can hold at most 2 MB of files")
    missing = [p for p in REQUIRED_PARTS if not any(e["part"] == p for e in entries)]
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"A version needs at least one {' and one '.join(missing)} file")
    return {
        "base_image": base_image,
        "test_command": test_command,
        "prepare_command": prepare_command,
        "files": sorted(entries, key=lambda e: (PARTS.index(e["part"]), e["path"])),
    }


async def transition(session: AsyncSession, version: EnvironmentVersion, to: str, *, actor: User | None, note: str | None = None) -> None:
    """Move a version along the state machine (D3), with an audit event in the vendor's log."""
    if to not in TRANSITIONS[version.status]:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A version that is {version.status.replace('_', ' ')} can't become {to.replace('_', ' ')}")
    before = version.status
    version.status = to
    version.status_changed_at = clock.now()
    environment = await session.get(Environment, version.environment_id)
    record(session, actor=actor, action=f"environment_version.{to}", entity_type="environment_version", entity_id=version.id,
           org_id=environment.org_id, before={"status": before}, after={"status": to, **({"note": note} if note else {})})


async def next_number(session: AsyncSession, environment_id: uuid.UUID) -> int:
    current = await session.scalar(
        text("SELECT coalesce(max(number), 0) FROM environment_versions WHERE environment_id = :e"), {"e": environment_id}
    )
    return current + 1
