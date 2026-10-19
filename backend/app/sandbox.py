"""Sandbox validation (EC3, D4, N8, N10, N21, A11, A12, build decision B5).

Run as its own process: `python -m app.sandbox`. It claims validation jobs from the Postgres
queue, fairly across vendors, and runs three checks on each submitted environment version,
and a fourth when it carries probe solutions:

- reference  the builder's reference solution must pass the grader (the task can be solved)
- empty      the untouched task files must fail it (the grader doesn't reward doing nothing, A11)
- repeat     the grader must give the same result for the same answer ten times running (A12)
- probes     every known-wrong answer the version carries must fail it (shortcuts are closed, D47)

Each check is two steps, each in a fresh container started through the Docker CLI with no
network, a read-only root filesystem, a small scratch directory, CPU, memory and process
limits, an unprivileged user and a time limit:

1. The attempt. Only the task files go in, with the reference solution laid over them for the
   reference and repeat checks. The builder's prepare command, if any, runs here. Only the task
   directory comes back out, so nothing the attempt does can plant files beside the grader.
2. Grading. The task files from step 1 plus the grader go in, and the test command runs.

The grader never enters step 1, and the reference solution's own files never enter any
container (N21). Files go in as a tar on stdin rather than as mounts, because the runner talks
to the host's Docker daemon, which can't see paths inside the runner's own container.
"""

import asyncio
import io
import logging
import subprocess
import tarfile
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, environments, registry
from app.config import settings
from app.models import EnvironmentVersion

log = logging.getLogger("sandbox")

REPEATS = 10
ATTEMPT_SECONDS = 30
GRADE_SECONDS = 30
REPEAT_SECONDS = 120
LIMITS = [
    "--network", "none", "--read-only", "--tmpfs", "/work:rw,exec,size=64m,uid=1000,gid=1000", "--tmpfs", "/tmp:rw,size=16m",
    "--memory", "256m", "--memory-swap", "256m", "--cpus", "1", "--pids-limit", "128",
    "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "1000:1000", "-e", "HOME=/work",
]

ATTEMPT = 'tar -x -C /work && cd /work && if [ -n "$PREPARE_COMMAND" ]; then sh -c "$PREPARE_COMMAND" 1>&2; fi; tar -c -C /work task'
GRADE = 'tar -x -C /work && cd /work && chmod -R a-w grader && sh -c "$TEST_COMMAND"'
REPEAT = ('tar -x -C /work && cd /work && chmod -R a-w grader && '
          f'for i in $(seq 1 {REPEATS}); do sh -c "$TEST_COMMAND" >/dev/null 2>&1; echo "RUN $?"; done')


@dataclass
class Run:
    exit_code: int | None
    stdout: bytes
    stderr: str
    timed_out: bool


@dataclass
class CheckResult:
    name: str
    passed: bool
    outcome: str  # pass, fail, timeout or error: what the grading step did
    detail: str
    duration_ms: int
    logs: str


def tar_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, data in sorted(files.items()):
            info = tarfile.TarInfo(path)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def files_of(tar_bytes: bytes) -> dict[str, bytes]:
    out = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.startswith("task/"):
                out[member.name] = tar.extractfile(member).read()
    return out


def run(image: str, files: dict[str, bytes], script: str, env: dict[str, str], seconds: int) -> Run:
    """One fresh, locked-down container; files in as a tar on stdin; killed at the time limit."""
    name = f"rl-check-{uuid.uuid4().hex[:12]}"
    command = ["docker", "run", "--rm", "-i", "--name", name, *LIMITS]
    for key, value in env.items():
        command += ["-e", f"{key}={value}"]
    command += [image, "sh", "-c", script]
    try:
        done = subprocess.run(command, input=tar_of(files), capture_output=True, timeout=seconds)
        return Run(done.returncode, done.stdout, done.stderr.decode(errors="replace"), False)
    except subprocess.TimeoutExpired as e:
        # Stopping the client doesn't stop the container; remove it, and wait until it's gone.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        return Run(None, e.stdout or b"", (e.stderr or b"").decode(errors="replace"), True)


def split(manifest: dict, contents: dict[str, bytes]) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, bytes]]:
    """Task files, grader files, and the reference solution mapped onto the task directory."""
    parts = {"task": {}, "grader": {}, "reference": {}, "probe": {}}
    for f in manifest["files"]:
        parts[f["part"]][f["path"]] = contents[f["hash"]]
    solved = {"task/" + p.removeprefix("reference/"): data for p, data in parts["reference"].items()}
    return parts["task"], parts["grader"], solved


def probes(manifest: dict, contents: dict[str, bytes]) -> dict[str, dict[str, bytes]]:
    """Each probe, by name, mapped onto the task directory like the reference solution."""
    out: dict[str, dict[str, bytes]] = {}
    for f in manifest["files"]:
        if f["part"] == "probe":
            _, name, rest = f["path"].split("/", 2)
            out.setdefault(name, {})["task/" + rest] = contents[f["hash"]]
    return out


def attempt(image: str, manifest: dict, files: dict[str, bytes]) -> tuple[dict[str, bytes] | None, str]:
    env = {"PREPARE_COMMAND": manifest.get("prepare_command") or ""}
    r = run(image, files, ATTEMPT, env, ATTEMPT_SECONDS)
    logs = f"== attempt ==\n{r.stderr}"
    if r.timed_out:
        return None, logs + f"\n(stopped after {ATTEMPT_SECONDS} s)"
    if r.exit_code != 0:
        return None, logs + f"\n(exit code {r.exit_code})"
    return files_of(r.stdout), logs


def check_reference(image, manifest, task, grader, solved) -> CheckResult:
    start = time.monotonic()
    after, logs = attempt(image, manifest, {**task, **solved})
    if after is None:
        return CheckResult("reference", False, "error", "The attempt step failed with the reference solution in place.", ms(start), logs)
    r = run(image, {**after, **grader}, GRADE, {"TEST_COMMAND": manifest["test_command"]}, GRADE_SECONDS)
    logs += f"\n== grading ==\n{r.stdout.decode(errors='replace')}{r.stderr}"
    if r.timed_out:
        return CheckResult("reference", False, "timeout", f"Grading the reference solution ran past {GRADE_SECONDS} s and was stopped.", ms(start), logs)
    if r.exit_code == 0:
        return CheckResult("reference", True, "pass", "The reference solution passes the grader.", ms(start), logs)
    return CheckResult("reference", False, "fail", f"The reference solution doesn't pass the grader (exit code {r.exit_code}).", ms(start), logs)


def check_empty(image, manifest, task, grader) -> CheckResult:
    start = time.monotonic()
    after, logs = attempt(image, manifest, dict(task))
    if after is None:
        return CheckResult("empty", False, "error", "The attempt step failed on the untouched task files.", ms(start), logs)
    r = run(image, {**after, **grader}, GRADE, {"TEST_COMMAND": manifest["test_command"]}, GRADE_SECONDS)
    logs += f"\n== grading ==\n{r.stdout.decode(errors='replace')}{r.stderr}"
    if r.timed_out:
        return CheckResult("empty", False, "timeout", f"Grading the untouched task files ran past {GRADE_SECONDS} s and was stopped.", ms(start), logs)
    if r.exit_code == 0:
        return CheckResult("empty", False, "pass", "The untouched task files pass the grader: it would reward an empty answer.", ms(start), logs)
    return CheckResult("empty", True, "fail", "The untouched task files fail the grader, as they should.", ms(start), logs)


def check_repeat(image, manifest, task, grader, solved) -> CheckResult:
    start = time.monotonic()
    after, logs = attempt(image, manifest, {**task, **solved})
    if after is None:
        return CheckResult("repeat", False, "error", "The attempt step failed with the reference solution in place.", ms(start), logs)
    r = run(image, {**after, **grader}, REPEAT, {"TEST_COMMAND": manifest["test_command"]}, REPEAT_SECONDS)
    codes = [int(line.split()[1]) for line in r.stdout.decode(errors="replace").splitlines() if line.startswith("RUN ")]
    logs += f"\n== grading, {REPEATS} runs ==\nexit codes: {codes}\n{r.stderr}"
    if r.timed_out or len(codes) < REPEATS:
        return CheckResult("repeat", False, "timeout", f"The {REPEATS} repeat runs didn't finish within {REPEAT_SECONDS} s.", ms(start), logs)
    passes = sum(1 for c in codes if c == 0)
    if passes in (0, REPEATS):
        return CheckResult("repeat", True, "pass" if passes else "fail", f"All {REPEATS} runs gave the same result.", ms(start), logs)
    return CheckResult("repeat", False, "fail", f"The grader gave different results for the same answer: {passes} of {REPEATS} runs passed.", ms(start), logs)


def check_probes(image, manifest, task, grader, probe_files: dict[str, dict[str, bytes]]) -> CheckResult:
    start, logs, passed = time.monotonic(), "", []
    for name, files in sorted(probe_files.items()):
        after, attempt_logs = attempt(image, manifest, {**task, **files})
        logs += f"== probe {name} ==\n{attempt_logs}"
        if after is None:
            continue  # a probe that can't even run is as good as failing
        r = run(image, {**after, **grader}, GRADE, {"TEST_COMMAND": manifest["test_command"]}, GRADE_SECONDS)
        logs += f"\n== grading ==\n{r.stdout.decode(errors='replace')}{r.stderr}\n"
        if r.exit_code == 0 and not r.timed_out:
            passed.append(name)
    if passed:
        return CheckResult("probes", False, "pass", f"A known-wrong answer passes the grader: {', '.join(passed)}. "
                           "The grader would reward that shortcut.", ms(start), logs)
    return CheckResult("probes", True, "fail", f"All {len(probe_files)} known-wrong answers fail the grader, as they should.", ms(start), logs)


def ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def connect_registry() -> None:
    """Sign the runner's Docker CLI in to the registry, and publish the base image there for vendors to build on (D50)."""
    host = settings.registry_host
    for attempt in range(30):  # the API, which signs registry tokens, may still be starting
        login = subprocess.run(["docker", "login", "-u", "platform", "--password-stdin", host],
                               input=settings.registry_platform_secret.encode(), capture_output=True)
        if login.returncode == 0:
            break
        time.sleep(2)
    login.check_returncode()
    base = f"{host}/{registry.BASE_REPO}:{settings.environment_base_image.rsplit(':', 1)[-1]}"
    subprocess.run(["docker", "tag", settings.environment_base_image, base], check=True)
    subprocess.run(["docker", "push", "-q", base], check=True, capture_output=True)


def run_checks(manifest: dict, contents: dict[str, bytes]) -> list[CheckResult]:
    task, grader, solved = split(manifest, contents)
    probe_files = probes(manifest, contents)
    image = manifest["base_image"]
    if image.startswith(settings.registry_host + "/"):
        # A vendor image, copied into the platform's namespace when the version was created.
        # Pulled up front, so the pull doesn't count against a check's time limit.
        subprocess.run(["docker", "pull", "-q", image], check=True, capture_output=True, timeout=600)
    return [
        check_reference(image, manifest, task, grader, solved),
        check_empty(image, manifest, task, grader),
        check_repeat(image, manifest, task, grader, solved),
        *([check_probes(image, manifest, task, grader, probe_files)] if probe_files else []),
    ]


# Fair share across vendors (D4, N10). Each queued job's score is its place in its own vendor's
# line plus how many of that vendor's jobs have been started since the vendor's oldest waiting
# job was queued. A vendor that has just been served waits behind one that hasn't, so one
# vendor's burst can't push another to the back.
CANDIDATES = text(
    """
    WITH queued AS (
        SELECT j.id, j.payload->>'vendor_id' AS vendor,
               row_number() OVER (PARTITION BY j.payload->>'vendor_id' ORDER BY j.id) AS place,
               min(j.created_at) OVER (PARTITION BY j.payload->>'vendor_id') AS waiting_since
        FROM jobs j WHERE j.status = 'queued' AND j.kind = 'validate_version'
    )
    SELECT q.id FROM queued q
    ORDER BY q.place + (
        SELECT count(*) FROM jobs s
        WHERE s.kind = 'validate_version' AND s.payload->>'vendor_id' = q.vendor AND s.started_at >= q.waiting_since
    ), q.id
    LIMIT 10
    """
)


async def claim(session: AsyncSession):
    for (job_id,) in (await session.execute(CANDIDATES)).all():
        row = (await session.execute(
            text("UPDATE jobs SET status = 'running', attempts = attempts + 1, started_at = clock_timestamp() "
                 "WHERE id = :id AND status = 'queued' RETURNING id, payload, attempts"),
            {"id": job_id},
        )).first()
        await session.commit()
        if row is not None:
            return row
    return None


async def process(session: AsyncSession, job, checks=run_checks) -> str:
    """Validate one version: validating, the checks, then failed or in review."""
    version = await session.get(EnvironmentVersion, uuid.UUID(job.payload["version_id"]))
    if version.status == "submitted":  # a retried job finds it validating already
        await environments.transition(session, version, "validating", actor=None)
        await session.commit()
    contents = {f["hash"]: environments.read(f["hash"]) for f in version.manifest["files"]}
    results = await asyncio.to_thread(checks, version.manifest, contents)
    for r in results:
        logs_hash = await environments.store(session, r.logs.encode())
        await session.execute(
            text("INSERT INTO validation_runs (version_id, check_name, passed, outcome, detail, duration_ms, logs_hash, created_at) "
                 "VALUES (:v, :c, :p, :o, :d, :ms, :h, :now)"),
            {"v": version.id, "c": r.name, "p": r.passed, "o": r.outcome, "d": r.detail, "ms": r.duration_ms, "h": logs_hash, "now": clock.now()},
        )
    failed = [r for r in results if not r.passed]
    outcome = "failed" if failed else "in_review"
    await environments.transition(session, version, outcome, actor=None, note=failed[0].detail if failed else f"All {len(results)} checks passed")
    await session.execute(text("UPDATE jobs SET status = 'done', finished_at = now() WHERE id = :id"), {"id": job.id})
    await session.commit()
    return outcome


MAX_ATTEMPTS = 3


async def handle(session: AsyncSession, job, checks=run_checks) -> str:
    """Process a job; if it crashes, try again, and after MAX_ATTEMPTS fail the version with the error,
    so it never waits in validating forever."""
    try:
        return await process(session, job, checks)
    except Exception as e:  # a broken job mustn't stop the runner
        await session.rollback()
        log.exception("validation job %s failed (attempt %s)", job.id, job.attempts)
        error = repr(e)[-2000:]
        if job.attempts < MAX_ATTEMPTS:
            await session.execute(text("UPDATE jobs SET status = 'queued', error = :e WHERE id = :id"), {"id": job.id, "e": error})
            await session.commit()
            return "retrying"
        await session.execute(text("UPDATE jobs SET status = 'failed', finished_at = now(), error = :e WHERE id = :id"), {"id": job.id, "e": error})
        version = await session.get(EnvironmentVersion, uuid.UUID(job.payload["version_id"]))
        if version.status == "submitted":
            await environments.transition(session, version, "validating", actor=None)
        if version.status == "validating":
            await environments.transition(session, version, "failed", actor=None,
                                          note=f"The checks couldn't run, after {MAX_ATTEMPTS} attempts: {error[:300]}")
        await session.commit()
        return "failed"


async def main() -> None:
    from app.db import SessionLocal

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    async with SessionLocal() as session:
        await clock.start(session)
    if settings.registry_platform_secret:
        connect_registry()
    log.info("sandbox runner started")
    while True:
        async with SessionLocal() as session:
            job = await claim(session)
            if job is None:
                await asyncio.sleep(1)
                continue
            log.info("version %s: %s", job.payload["version_id"], await handle(session, job))


if __name__ == "__main__":
    asyncio.run(main())
