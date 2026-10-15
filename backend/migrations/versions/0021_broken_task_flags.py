"""Workers' flags on broken tasks, and paused or retired tasks (DC5, D43)

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_flags",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("worker_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assignment_id", UUID(as_uuid=True), sa.ForeignKey("assignments.id"), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "worker_id", name="task_flags_once_per_worker"),
    )
    # Tasks never change, so their state after flags lives here. Paused: out of routing until a
    # researcher decides. Retired: out of routing and out of every measure and dataset. Restored:
    # routed again; flags before restored_at don't count toward pausing it again.
    op.create_table(
        "broken_tasks",
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('paused', 'retired', 'restored')", name="broken_tasks_status"),
    )
    # A worker who flags the task they were given hands the assignment back without answering.
    op.drop_constraint("assignments_status", "assignments")
    op.create_check_constraint("assignments_status", "assignments", "status IN ('open', 'submitted', 'expired', 'flagged')")


def downgrade() -> None:
    op.drop_constraint("assignments_status", "assignments")
    op.create_check_constraint("assignments_status", "assignments", "status IN ('open', 'submitted', 'expired')")
    op.drop_table("broken_tasks")
    op.drop_table("task_flags")
