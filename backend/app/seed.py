"""Seed the organizations and demo users. Safe to run repeatedly."""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Org, OrgKind, Role, User

# Fictional organizations and people. Anthropic is the platform operator; the vendors are made up.
SEED: list[tuple[str, OrgKind, list[tuple[Role, str]]]] = [
    ("Anthropic", OrgKind.INTERNAL, [(Role.RESEARCHER, "Rae Okafor"), (Role.DATA_OPS, "Sam Lindqvist")]),
    ("Northwind Data", OrgKind.VENDOR, [(Role.VENDOR_ADMIN, "Priya Raman"), (Role.WORKER, "Tomás Ruiz"), (Role.WORKER, "Ada Mensah")]),
    ("Kestrel Labs", OrgKind.VENDOR, [(Role.VENDOR_ADMIN, "Jonah Weiss"), (Role.WORKER, "Mei Tanaka"), (Role.WORKER, "Luca Romano")]),
    ("Brightline Annotation", OrgKind.VENDOR, [(Role.VENDOR_ADMIN, "Grace Adeyemi"), (Role.WORKER, "Omar Haddad"), (Role.WORKER, "Nina Petrova")]),
]


async def seed(session: AsyncSession) -> None:
    for org_name, kind, people in SEED:
        org = await session.scalar(select(Org).where(Org.name == org_name))
        if org is None:
            org = Org(name=org_name, kind=kind)
            session.add(org)
            await session.flush()
        for role, name in people:
            exists = await session.scalar(select(User).where(User.org_id == org.id, User.display_name == name))
            if exists is None:
                session.add(User(org_id=org.id, role=role, display_name=name))
    await session.commit()


async def main() -> None:
    async with SessionLocal() as session:
        await seed(session)
    print("Seed complete")


if __name__ == "__main__":
    asyncio.run(main())
