"""Self-serve onboarding (VM1, VM3, N14, D46).

Data ops creates a vendor and invites its admin. The vendor then proves its setup on samples
that run on the real path but feed nothing: a sample task set its workers answer, and a sample
environment brief its tooling submits to. The checklist is computed from what happened, never
from what the vendor says, and real work can be allocated only once it is complete.
"""

import asyncio
from decimal import Decimal

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, demo_content, registry
from app.audit import record
from app.config import settings
from app.models import Allocation, Capability, Contract, GoldAnswer, Invite, Org, PriceUnit, Project, Role, Task, TaskSchema, User
from app.work import route_tasks

SAMPLE_TASKS_PROJECT = "Onboarding: sample tasks"
SAMPLE_BRIEF = "Onboarding: sample environment"
SAMPLE_COUNT = 5
SAMPLE_PASS = 4  # correct answers out of SAMPLE_COUNT
SAMPLE_BRIEF_CONTENT = {
    "description": "Submit any small environment of your own, from your own tooling with one of your API tokens, on an image "
                   "you pushed to your registry namespace. It must pass the sandbox's checks. Nobody reviews it and training never sees it.",
    "languages": ["python"],
    "constraints": ["Submitted with an API token, not by a person", "Names your own image, pinned by digest"],
    "difficulty_mix": {},
    "review_rubric": ["Passes the sandbox's checks"],
}


async def ensure_projects(session: AsyncSession) -> None:
    """The two onboarding projects every new vendor is allocated to. Created once, by the seed."""
    researcher = await session.scalar(select(User).where(User.role == Role.RESEARCHER))
    if not await session.scalar(select(Project.id).where(Project.name == SAMPLE_TASKS_PROJECT)):
        project = Project(name=SAMPLE_TASKS_PROJECT, created_by=researcher.id, task_type=demo_content.TASK_TYPE, current_schema_version=1,
                          target_volume=SAMPLE_COUNT, overlap=10, gold_rate=Decimal("0"), onboarding=True)
        session.add(project)
        await session.flush()
        session.add(TaskSchema(project_id=project.id, version=1, answer_schema=demo_content.ANSWER_SCHEMA, instructions=demo_content.INSTRUCTIONS))
        await session.flush()
        # Every sample has a known answer: the sample set is scored like gold, but it's its own set (D8).
        for generated in demo_content.generate(SAMPLE_COUNT, seed=101):
            task = Task(project_id=project.id, schema_version=1, payload=generated.payload)
            session.add(task)
            await session.flush()
            session.add(GoldAnswer(task_id=task.id, answer={"choice": generated.better}))
    if not await session.scalar(select(Project.id).where(Project.name == SAMPLE_BRIEF)):
        session.add(Project(name=SAMPLE_BRIEF, created_by=researcher.id, task_type="environment", current_schema_version=1,
                            target_volume=1, overlap=1, gold_rate=Decimal("0"), kind="environment", brief=SAMPLE_BRIEF_CONTENT,
                            onboarding=True))
    await session.flush()


async def start(session: AsyncSession, org: Org) -> None:
    """Allocate a new vendor to the samples for its capabilities, under unpaid onboarding contracts."""
    samples = {Capability.HUMAN_WORK: (SAMPLE_TASKS_PROJECT, PriceUnit.TASK), Capability.ENVIRONMENTS: (SAMPLE_BRIEF, PriceUnit.ENVIRONMENT)}
    for capability in org.capabilities:
        name, unit = samples[capability]
        project = await session.scalar(select(Project).where(Project.name == name))
        contract = Contract(vendor_id=org.id, kind=capability, price=Decimal("0"), price_unit=unit, effective_from=clock.now().date())
        session.add(contract)
        await session.flush()
        session.add(Allocation(project_id=project.id, vendor_id=org.id, contract_id=contract.id, share=Decimal("1")))
        await session.flush()
        if project.kind == "human_data":
            await route_tasks(session, project)


def _pushed(org_name: str) -> bool:
    """Whether anything is in the vendor's registry namespace, from the registry's own catalog."""
    token = registry.mint("platform", [{"type": "registry", "name": "catalog", "actions": ["*"]}])
    # shortcut: reads the first page of the catalog (1,000 repositories); page through it past that.
    r = httpx.get(f"{settings.registry_url}/v2/_catalog", params={"n": 1000}, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    r.raise_for_status()
    return any(repo.startswith(registry.namespace(org_name) + "/") for repo in r.json().get("repositories") or [])


SAMPLE_ANSWERS = text("""
SELECT u.display_name, count(*) AS answered, count(*) FILTER (WHERE s.answer @> g.answer) AS correct, max(s.submitted_at) AS last_at
FROM submissions s JOIN projects p ON p.id = s.project_id AND p.name = :project
JOIN gold_answers g ON g.task_id = s.task_id JOIN users u ON u.id = s.worker_id
WHERE s.vendor_id = :org GROUP BY u.display_name ORDER BY correct DESC, answered DESC
""")

SAMPLE_VERSIONS = text("""
SELECT ev.status, ev.created_by_token IS NOT NULL AS by_token, ev.manifest ? 'vendor_image' AS on_own_image, ev.created_at, ev.status_changed_at,
       (SELECT detail FROM validation_runs r WHERE r.version_id = ev.id AND NOT r.passed ORDER BY r.created_at LIMIT 1) AS failure
FROM environment_versions ev JOIN environments e ON e.id = ev.environment_id JOIN projects p ON p.id = e.project_id AND p.name = :brief
WHERE e.org_id = :org ORDER BY ev.created_at
""")


def item(key: str, label: str, done: bool, detail: str, at=None) -> dict:
    return {"key": key, "label": label, "done": done, "detail": detail, "at": at}


async def checklist(session: AsyncSession, org: Org) -> dict:
    """The vendor's checklist, computed from what happened. Records the moment it's first complete."""
    invite = await session.scalar(select(Invite).where(Invite.org_id == org.id, Invite.role == Role.VENDOR_ADMIN).order_by(Invite.created_at))
    admin = await session.scalar(select(User).where(User.org_id == org.id, User.role == Role.VENDOR_ADMIN).order_by(User.created_at))
    items = [item("admin", "Admin signed in", admin is not None,
                  f"{admin.display_name} joined" if admin else "Open the invite link data ops sent you",
                  invite.accepted_at if invite else None)]
    first_accepted = []
    if Capability.HUMAN_WORK in org.capabilities:
        worker = await session.scalar(select(User).where(User.org_id == org.id, User.role == Role.WORKER).order_by(User.created_at))
        items.append(item("worker", "A worker added", worker is not None,
                          f"{worker.display_name} added" if worker else "Add a worker under Workspaces", worker.created_at if worker else None))
        rows = (await session.execute(SAMPLE_ANSWERS, {"project": SAMPLE_TASKS_PROJECT, "org": org.id})).all()
        passed = next((r for r in rows if r.answered >= SAMPLE_COUNT and r.correct >= SAMPLE_PASS), None)
        best = passed or (rows[0] if rows else None)
        detail = (f"{best.display_name} answered {best.answered} of {SAMPLE_COUNT}, {best.correct} correct"
                  + ("" if passed or best.answered < SAMPLE_COUNT else f": below the bar of {SAMPLE_PASS}; another worker can try")
                  if best else f"A worker answers the {SAMPLE_COUNT} sample tasks; {SAMPLE_PASS} must be right")
        items.append(item("samples", "Sample tasks answered correctly", passed is not None, detail, passed.last_at if passed else None))
        if passed:
            first_accepted.append(passed.last_at)
    if Capability.ENVIRONMENTS in org.capabilities:
        used = await session.scalar(text("SELECT min(last_used_at) FROM api_tokens WHERE org_id = :o"), {"o": org.id})
        items.append(item("token", "An API token created and used", used is not None,
                          "Your tooling has signed in with a token" if used else "Create a token under API tokens and use it", used))
        if settings.registry_url:
            pushed = await asyncio.to_thread(_pushed, org.name)
            items.append(item("image", "An image pushed to your registry namespace", pushed,
                              f"Found in {registry.namespace(org.name)}/" if pushed else f"docker push to {settings.registry_host}/{registry.namespace(org.name)}/…"))
        versions = [v for v in (await session.execute(SAMPLE_VERSIONS, {"brief": SAMPLE_BRIEF, "org": org.id})).all() if v.by_token]
        ok = next((v for v in versions if v.status in ("in_review", "approved") and (v.on_own_image or not settings.registry_url)), None)
        last = versions[-1] if versions else None
        if ok:
            detail = "Passed the sandbox's checks"
        elif last is None:
            detail = "Submit a version to the sample brief with a token"
        elif last.status == "failed":
            detail = f"Your last version failed a check: {last.failure}"
        elif last.status in ("in_review", "approved"):
            detail = "It passed, but name your own image: the sample proves your runtime works"
        else:
            detail = "Your version is being checked"
        items.append(item("sample_environment", "A sample environment passes the sandbox", ok is not None, detail,
                          ok.status_changed_at if ok else None))
        if ok:
            first_accepted.append(ok.status_changed_at)
    complete = all(i["done"] for i in items)
    if complete and org.onboarded_at is None:
        org.onboarded_at = clock.now()
        record(session, actor=None, action="vendor.onboarded", entity_type="org", entity_id=org.id, org_id=org.id,
               after={"items": [i["key"] for i in items]})
        await session.commit()
    return {"org_id": str(org.id), "org_name": org.name, "capabilities": org.capabilities, "items": items, "complete": complete,
            "invited_at": invite.created_at if invite else None, "first_accepted_at": min(first_accepted) if first_accepted else None,
            "onboarded_at": org.onboarded_at, "namespace": registry.namespace(org.name),
            "registry_host": settings.registry_host if settings.registry_url else None}
