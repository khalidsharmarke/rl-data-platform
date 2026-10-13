import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record
from app.auth import current_user
from app.db import get_session
from app.models import PRICE_UNITS, Capability, Contract, OrgKind, PriceUnit, Role, User
from app.scoping import require_roles, visible_org

router = APIRouter(prefix="/orgs/{org_id}/contracts")


class ContractIn(BaseModel):
    kind: Capability
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=4)
    price_unit: PriceUnit
    effective_from: date
    effective_to: date | None = None


class ContractOut(BaseModel):
    id: uuid.UUID
    vendor_id: uuid.UUID
    kind: str
    price: Decimal
    price_unit: str
    effective_from: date
    effective_to: date | None


def contract_snapshot(c: Contract) -> dict:
    return {"kind": c.kind, "price": f"{c.price:.4f}", "price_unit": c.price_unit, "effective_from": c.effective_from.isoformat(),
            "effective_to": c.effective_to.isoformat() if c.effective_to else None}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_contract(
    org_id: uuid.UUID,
    body: ContractIn,
    user: User = Depends(require_roles(Role.DATA_OPS)),
    session: AsyncSession = Depends(get_session),
) -> ContractOut:
    org = await visible_org(org_id, user, session)
    if org.kind != OrgKind.VENDOR:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Contracts are with vendors")
    if body.kind not in org.capabilities:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{org.name} doesn't supply {body.kind}")
    if body.price_unit not in PRICE_UNITS[body.kind]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{body.kind} can't be priced per {body.price_unit}")
    if body.effective_to and body.effective_to < body.effective_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Contract ends before it starts")
    contract = Contract(vendor_id=org.id, **body.model_dump())
    session.add(contract)
    await session.flush()
    record(session, actor=user, action="contract.created", entity_type="contract", entity_id=contract.id, org_id=org.id, after=contract_snapshot(contract))
    await session.commit()
    return ContractOut.model_validate(contract, from_attributes=True)


@router.get("")
async def list_contracts(
    org_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[ContractOut]:
    org = await visible_org(org_id, user, session)
    if user.role not in (Role.DATA_OPS, Role.VENDOR_ADMIN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role can't see contracts")
    contracts = await session.scalars(select(Contract).where(Contract.vendor_id == org.id).order_by(Contract.effective_from))
    return [ContractOut.model_validate(c, from_attributes=True) for c in contracts]
