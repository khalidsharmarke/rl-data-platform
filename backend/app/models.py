import secrets
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class OrgKind(StrEnum):
    INTERNAL = "internal"
    VENDOR = "vendor"


class Capability(StrEnum):
    HUMAN_WORK = "human_work"
    ENVIRONMENTS = "environments"


class Role(StrEnum):
    RESEARCHER = "researcher"
    DATA_OPS = "data_ops"
    VENDOR_ADMIN = "vendor_admin"
    VENDOR_REVIEWER = "vendor_reviewer"
    WORKER = "worker"
    ENVIRONMENT_BUILDER = "environment_builder"


INTERNAL_ROLES = {Role.RESEARCHER, Role.DATA_OPS}
VENDOR_ROLES = {Role.VENDOR_ADMIN, Role.VENDOR_REVIEWER, Role.WORKER, Role.ENVIRONMENT_BUILDER}


def new_handle() -> str:
    return "U-" + secrets.token_hex(3).upper()


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    domains: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="org")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    role: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    handle: Mapped[str] = mapped_column(Text, unique=True, default=new_handle)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    org: Mapped[Org] = relationship(back_populates="users")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str] = mapped_column(Text)
    org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orgs.id"))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
