import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record, snapshot
from app.auth import current_user
from app.db import get_session
from app.models import INTERNAL_ROLES, VENDOR_ROLES, Capability, Org, OrgKind, Role, User
from app.scoping import is_internal, require_roles, shows_identity, visible_org

router = APIRouter(prefix="/orgs")


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    capabilities: list[str]
    domains: list[str]

    @classmethod
    def of(cls, org: Org) -> "OrgOut":
        return cls(id=org.id, name=org.name, kind=org.kind, capabilities=org.capabilities, domains=org.domains)


class MemberOut(BaseModel):
    id: uuid.UUID
    handle: str
    role: str
    # Present only when the viewer may see this person's identity (D12).
    display_name: str | None


class VendorIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    capabilities: list[Capability] = Field(min_length=1)
    domains: list[str] = []


class CapabilitiesIn(BaseModel):
    capabilities: list[Capability] = Field(min_length=1)
    domains: list[str] = []


class MemberIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    role: Role


@router.get("")
async def list_orgs(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> list[OrgOut]:
    query = select(Org).order_by(Org.kind, Org.name)
    if not is_internal(user):
        query = query.where(Org.id == user.org_id)
    return [OrgOut.of(o) for o in await session.scalars(query)]


@router.get("/{org_id}")
async def get_org(org_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> OrgOut:
    return OrgOut.of(await visible_org(org_id, user, session))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_vendor(
    body: VendorIn,
    user: User = Depends(require_roles(Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> OrgOut:
    if await session.scalar(select(Org).where(Org.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A workspace with that name exists")
    org = Org(name=body.name, kind=OrgKind.VENDOR, capabilities=sorted(set(body.capabilities)), domains=body.domains)
    session.add(org)
    await session.flush()
    record(session, actor=user, action="org.created", entity_type="org", entity_id=org.id, org_id=org.id, after=snapshot(org))
    await session.commit()
    return OrgOut.of(org)


@router.put("/{org_id}/capabilities")
async def set_capabilities(
    org_id: uuid.UUID,
    body: CapabilitiesIn,
    user: User = Depends(require_roles(Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> OrgOut:
    org = await visible_org(org_id, user, session)
    if org.kind != OrgKind.VENDOR:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only vendor workspaces have capabilities")
    before = snapshot(org)
    org.capabilities = sorted(set(body.capabilities))
    org.domains = body.domains
    record(session, actor=user, action="org.capabilities_set", entity_type="org", entity_id=org.id, org_id=org.id, before=before, after=snapshot(org))
    await session.commit()
    return OrgOut.of(org)


@router.get("/{org_id}/members")
async def list_members(
    org_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[MemberOut]:
    org = await visible_org(org_id, user, session)
    members = await session.scalars(select(User).where(User.org_id == org.id).order_by(User.role, User.handle))
    return [
        MemberOut(id=m.id, handle=m.handle, role=m.role, display_name=m.display_name if shows_identity(user, m) else None)
        for m in members
    ]


@router.post("/{org_id}/members", status_code=status.HTTP_201_CREATED)
async def add_member(
    org_id: uuid.UUID,
    body: MemberIn,
    user: User = Depends(require_roles(Role.VENDOR_ADMIN, Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> MemberOut:
    org = await visible_org(org_id, user, session)
    # Vendor admins manage their own workspace; data ops sets up vendor admins and Anthropic staff.
    if user.role == Role.VENDOR_ADMIN and org.id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    allowed = INTERNAL_ROLES if org.kind == OrgKind.INTERNAL else VENDOR_ROLES
    if user.role == Role.DATA_OPS and org.kind == OrgKind.VENDOR:
        allowed = {Role.VENDOR_ADMIN}
    if body.role not in allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Can't add a {body.role} here")
    member = User(org_id=org.id, role=body.role, display_name=body.display_name)
    session.add(member)
    await session.flush()
    record(session, actor=user, action="user.added", entity_type="user", entity_id=member.id, org_id=org.id, after=snapshot(member))
    await session.commit()
    return MemberOut(id=member.id, handle=member.handle, role=member.role, display_name=member.display_name if shows_identity(user, member) else None)
