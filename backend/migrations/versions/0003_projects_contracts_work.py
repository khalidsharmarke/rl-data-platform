"""Contracts, projects, tasks, hidden gold, allocations, routing, assignments and submissions

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

IMMUTABLE = ["task_schemas", "tasks", "gold_answers", "submissions"]


def uuid_pk() -> sa.Column:
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    # Raw cost inputs (VM7, PRD PD3): price and unit as agreed, never a derived cost.
    op.create_table(
        "contracts",
        uuid_pk(),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("price", sa.Numeric(12, 4), nullable=False),
        sa.Column("price_unit", sa.Text, nullable=False),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("effective_to", sa.Date, nullable=True),
        created_at(),
        sa.CheckConstraint("kind IN ('human_work', 'environments')", name="contracts_kind"),
        sa.CheckConstraint("price_unit IN ('hour', 'task', 'environment')", name="contracts_price_unit"),
        sa.CheckConstraint("price > 0", name="contracts_price_positive"),
        sa.CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="contracts_dates"),
    )

    op.create_table(
        "projects",
        uuid_pk(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_type", sa.Text, nullable=False),
        sa.Column("current_schema_version", sa.Integer, nullable=False),
        sa.Column("target_volume", sa.Integer, nullable=False),
        sa.Column("deadline", sa.Date, nullable=True),
        sa.Column("overlap", sa.Integer, nullable=False),
        sa.Column("gold_rate", sa.Numeric(4, 3), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        created_at(),
        sa.CheckConstraint("overlap BETWEEN 1 AND 10", name="projects_overlap"),
        sa.CheckConstraint("gold_rate >= 0 AND gold_rate < 1", name="projects_gold_rate"),
        sa.CheckConstraint("status IN ('active', 'paused', 'closed')", name="projects_status"),
    )

    # A task design version (N19): answer schema plus instructions, never edited once written.
    op.create_table(
        "task_schemas",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("answer_schema", JSONB, nullable=False),
        sa.Column("instructions", sa.Text, nullable=False),
        created_at(),
    )

    op.create_table(
        "tasks",
        uuid_pk(),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        # A serial position, assigned after shuffling, so claim order reveals nothing about gold.
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False),
        created_at(),
        sa.ForeignKeyConstraint(["project_id", "schema_version"], ["task_schemas.project_id", "task_schemas.version"]),
    )
    op.create_index("tasks_project_seq", "tasks", ["project_id", "seq"])

    # Gold answers live apart from tasks; worker- and vendor-facing code never reads this table (D8).
    op.create_table(
        "gold_answers",
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("answer", JSONB, nullable=False),
        sa.Column("gold_set_version", sa.Integer, nullable=False, server_default="1"),
        created_at(),
    )

    op.create_table(
        "allocations",
        uuid_pk(),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("contract_id", UUID(as_uuid=True), sa.ForeignKey("contracts.id"), nullable=False),
        sa.Column("share", sa.Numeric(4, 3), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        created_at(),
        sa.CheckConstraint("share > 0 AND share <= 1", name="allocations_share"),
        sa.CheckConstraint("status IN ('active', 'paused', 'ended')", name="allocations_status"),
        sa.UniqueConstraint("project_id", "vendor_id", name="allocations_one_per_vendor"),
    )

    # Which vendor may work on which task. Regular tasks go to one vendor; gold tasks to every vendor.
    op.create_table(
        "task_routes",
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), primary_key=True),
        sa.Column("routed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("task_routes_vendor", "task_routes", ["vendor_id"])

    op.create_table(
        "assignments",
        uuid_pk(),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("worker_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("allocation_id", UUID(as_uuid=True), sa.ForeignKey("allocations.id"), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.CheckConstraint("status IN ('open', 'submitted', 'expired')", name="assignments_status"),
        # A worker answers a task at most once (N7).
        sa.UniqueConstraint("task_id", "worker_id", name="assignments_once_per_worker"),
    )
    op.create_index("assignments_task_vendor", "assignments", ["task_id", "vendor_id"])
    op.create_index("assignments_worker_open", "assignments", ["worker_id"], postgresql_where=sa.text("status = 'open'"))

    # Each submission carries its full lineage (D14) and platform-measured time on task (DC9).
    op.create_table(
        "submissions",
        uuid_pk(),
        sa.Column("assignment_id", UUID(as_uuid=True), sa.ForeignKey("assignments.id"), nullable=False, unique=True),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("worker_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("allocation_id", UUID(as_uuid=True), sa.ForeignKey("allocations.id"), nullable=False),
        sa.Column("contract_id", UUID(as_uuid=True), sa.ForeignKey("contracts.id"), nullable=False),
        sa.Column("answer", JSONB, nullable=False),
        sa.Column("client_signals", JSONB, nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("time_on_task_ms", sa.Integer, nullable=False),
    )
    op.create_index("submissions_project_vendor", "submissions", ["project_id", "vendor_id"])
    op.create_index("submissions_worker", "submissions", ["worker_id"])

    op.execute(
        """
        CREATE FUNCTION reject_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in IMMUTABLE:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_change()"
        )


def downgrade() -> None:
    for table in ["submissions", "assignments", "task_routes", "allocations", "gold_answers", "tasks", "task_schemas", "projects", "contracts"]:
        op.drop_table(table)
    op.execute("DROP FUNCTION reject_change()")
