"""Seed the organizations and demo users. Safe to run repeatedly."""

import asyncio
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record, snapshot
from app.db import SessionLocal
from app import demo_content
from app.models import Allocation, Capability, Contract, GoldAnswer, Org, OrgKind, PriceUnit, Project, Role, Task, TaskSchema, User
from app.work import route_tasks

# Fictional organizations and people. Anthropic is the platform operator; the vendors are made up.
HUMAN, ENVS = Capability.HUMAN_WORK, Capability.ENVIRONMENTS

SEED: list[tuple[str, OrgKind, list[Capability], list[tuple[Role, str]]]] = [
    ("Anthropic", OrgKind.INTERNAL, [], [(Role.RESEARCHER, "Rae Okafor"), (Role.DATA_OPS, "Sam Lindqvist")]),
    ("Northwind Data", OrgKind.VENDOR, [HUMAN], [(Role.VENDOR_ADMIN, "Priya Raman"), (Role.WORKER, "Tomás Ruiz"), (Role.WORKER, "Ada Mensah")]),
    ("Kestrel Labs", OrgKind.VENDOR, [HUMAN, ENVS], [(Role.VENDOR_ADMIN, "Jonah Weiss"), (Role.WORKER, "Mei Tanaka"), (Role.WORKER, "Luca Romano")]),
    ("Brightline Annotation", OrgKind.VENDOR, [HUMAN], [(Role.VENDOR_ADMIN, "Grace Adeyemi"), (Role.WORKER, "Omar Haddad"), (Role.WORKER, "Nina Petrova")]),
]


async def seed(session: AsyncSession) -> None:
    # Seeding is a system action: audit events have no actor.
    for org_name, kind, capabilities, people in SEED:
        org = await session.scalar(select(Org).where(Org.name == org_name))
        if org is None:
            org = Org(name=org_name, kind=kind, capabilities=sorted(capabilities))
            session.add(org)
            await session.flush()
            record(session, actor=None, action="org.created", entity_type="org", entity_id=org.id, org_id=org.id, after=snapshot(org))
        for role, name in people:
            exists = await session.scalar(select(User).where(User.org_id == org.id, User.display_name == name))
            if exists is None:
                user = User(org_id=org.id, role=role, display_name=name)
                session.add(user)
                await session.flush()
                record(session, actor=None, action="user.added", entity_type="user", entity_id=user.id, org_id=org.id, after=snapshot(user))
    await seed_project(session)
    await session.commit()


# The demo story's three contracts: per task, hourly, and a cheaper per-task rate.
CONTRACTS = {
    "Northwind Data": (Decimal("0.80"), PriceUnit.TASK, Decimal("0.40")),
    "Kestrel Labs": (Decimal("38.00"), PriceUnit.HOUR, Decimal("0.35")),
    "Brightline Annotation": (Decimal("0.55"), PriceUnit.TASK, Decimal("0.25")),
}
SAMPLE_PROJECT = "Which answer is more helpful? (sample)"
SAMPLE_TASKS = 60
SAMPLE_GOLD_EVERY = 20  # 5% gold


async def seed_project(session: AsyncSession) -> None:
    if await session.scalar(select(Project).where(Project.name == SAMPLE_PROJECT)):
        return
    researcher = await session.scalar(select(User).where(User.role == Role.RESEARCHER))
    project = Project(
        name=SAMPLE_PROJECT, created_by=researcher.id, task_type=demo_content.TASK_TYPE, current_schema_version=1,
        target_volume=3000, overlap=2, gold_rate=Decimal("0.05"),
    )
    session.add(project)
    await session.flush()
    session.add(TaskSchema(project_id=project.id, version=1, answer_schema=demo_content.ANSWER_SCHEMA, instructions=demo_content.INSTRUCTIONS))
    await session.flush()
    for i, generated in enumerate(demo_content.generate(SAMPLE_TASKS)):
        task = Task(project_id=project.id, schema_version=1, payload=generated.payload)
        session.add(task)
        await session.flush()
        if i % SAMPLE_GOLD_EVERY == 0:
            session.add(GoldAnswer(task_id=task.id, answer={"choice": generated.better}))
    record(session, actor=researcher, action="project.created", entity_type="project", entity_id=project.id, org_id=researcher.org_id,
           after={"name": project.name, "tasks": SAMPLE_TASKS, "gold": SAMPLE_TASKS // SAMPLE_GOLD_EVERY})

    for vendor_name, (price, unit, share) in CONTRACTS.items():
        vendor = await session.scalar(select(Org).where(Org.name == vendor_name))
        contract = Contract(vendor_id=vendor.id, kind=Capability.HUMAN_WORK, price=price, price_unit=unit, effective_from=date(2026, 1, 1))
        session.add(contract)
        await session.flush()
        record(session, actor=None, action="contract.created", entity_type="contract", entity_id=contract.id, org_id=vendor.id,
               after={"kind": contract.kind, "price": f"{price:.4f}", "price_unit": unit})
        allocation = Allocation(project_id=project.id, vendor_id=vendor.id, contract_id=contract.id, share=share)
        session.add(allocation)
        await session.flush()
        record(session, actor=None, action="allocation.created", entity_type="allocation", entity_id=allocation.id, org_id=vendor.id,
               after={"project_id": str(project.id), "share": str(share)})
    await route_tasks(session, project)


async def main() -> None:
    async with SessionLocal() as session:
        await seed(session)
    print("Seed complete")


if __name__ == "__main__":
    asyncio.run(main())
