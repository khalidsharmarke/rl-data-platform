"""Clean-up (removals) and exported datasets with row-level lineage

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def uuid_pk() -> sa.Column:
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def upgrade() -> None:
    op.create_table(
        "cleanups",
        uuid_pk(),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("worker_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("requested_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed", sa.Integer, nullable=False),
        sa.Column("tasks_reopened", sa.Integer, nullable=False),
        sa.CheckConstraint("mode IN ('discard', 'relabel')", name="cleanups_mode"),
    )
    # Submissions are immutable, so removal is recorded beside them (AB6).
    op.create_table(
        "removals",
        sa.Column("submission_id", UUID(as_uuid=True), sa.ForeignKey("submissions.id"), primary_key=True),
        sa.Column("cleanup_id", UUID(as_uuid=True), sa.ForeignKey("cleanups.id"), nullable=False),
        # Re-label: the task's slot opens again for another worker at the same vendor.
        sa.Column("requeue", sa.Boolean, nullable=False),
    )
    op.create_table(
        "datasets",
        uuid_pk(),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric_version", sa.Text, nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("uri", sa.Text, nullable=False),
        sa.Column("supersedes", UUID(as_uuid=True), sa.ForeignKey("datasets.id"), nullable=True),
    )
    # Every dataset row references its source submission (D14).
    op.create_table(
        "dataset_rows",
        sa.Column("dataset_id", UUID(as_uuid=True), sa.ForeignKey("datasets.id"), primary_key=True),
        sa.Column("submission_id", UUID(as_uuid=True), sa.ForeignKey("submissions.id"), primary_key=True),
    )
    op.create_index("dataset_rows_submission", "dataset_rows", ["submission_id"])
    for table in ("removals", "datasets", "dataset_rows", "cleanups"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_change()")


def downgrade() -> None:
    for t in ("dataset_rows", "datasets", "removals", "cleanups"):
        op.drop_table(t)
