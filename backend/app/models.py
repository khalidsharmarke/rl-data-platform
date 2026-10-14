import secrets
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Identity, Integer, Numeric, Text, func
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
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class PriceUnit(StrEnum):
    HOUR = "hour"
    TASK = "task"
    ENVIRONMENT = "environment"


# Which price units make sense for each kind of work.
PRICE_UNITS: dict[str, set[str]] = {
    Capability.HUMAN_WORK: {PriceUnit.HOUR, PriceUnit.TASK},
    Capability.ENVIRONMENTS: {PriceUnit.ENVIRONMENT, PriceUnit.HOUR},
}


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    kind: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    price_unit: Mapped[str] = mapped_column(Text)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def in_effect(self, on: date) -> bool:
        return self.effective_from <= on and (self.effective_to is None or on <= self.effective_to)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    task_type: Mapped[str] = mapped_column(Text)
    current_schema_version: Mapped[int] = mapped_column(Integer)
    target_volume: Mapped[int] = mapped_column(Integer)
    deadline: Mapped[date | None] = mapped_column(Date)
    overlap: Mapped[int] = mapped_column(Integer)
    gold_rate: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    quality_bar: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0.800"))
    min_gold_results: Mapped[int] = mapped_column(Integer, default=5)
    # Release stage (DC10, PD7): the least and the most an accepted answer waits.
    release_after_minutes: Mapped[int] = mapped_column(Integer, default=60)
    release_cap_minutes: Mapped[int] = mapped_column(Integer, default=24 * 60)
    status: Mapped[str] = mapped_column(Text, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskSchema(Base):
    __tablename__ = "task_schemas"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    answer_schema: Mapped[dict[str, Any]] = mapped_column(JSONB)
    instructions: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    schema_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    seq: Mapped[int] = mapped_column(BigInteger, Identity())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GoldAnswer(Base):
    __tablename__ = "gold_answers"

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    gold_set_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Allocation(Base):
    __tablename__ = "allocations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id"))
    share: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    status: Mapped[str] = mapped_column(Text, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskRoute(Base):
    __tablename__ = "task_routes"

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"), primary_key=True)
    routed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_full: Mapped[bool] = mapped_column(Boolean, default=False)


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    worker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    allocation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("allocations.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, default="open")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assignments.id"), unique=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    schema_version: Mapped[int] = mapped_column(Integer)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orgs.id"))
    worker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    allocation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("allocations.id"))
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id"))
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB)
    client_signals: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    time_on_task_ms: Mapped[int] = mapped_column(Integer)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    submission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("submissions.id"))
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    verdict: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GoldRetirement(Base):
    __tablename__ = "gold_retirements"

    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gold_answers.task_id"), primary_key=True)
    retired_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    retired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
