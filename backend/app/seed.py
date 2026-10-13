"""Seed the organizations and demo users. Safe to run repeatedly."""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record, snapshot
from app.db import SessionLocal
from app.models import Capability, Org, OrgKind, Role, User

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
    await session.commit()


async def main() -> None:
    async with SessionLocal() as session:
        await seed(session)
    print("Seed complete")


if __name__ == "__main__":
    asyncio.run(main())
