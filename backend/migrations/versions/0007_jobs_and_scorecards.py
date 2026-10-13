"""Background job queue and scorecard rows

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The job queue is a Postgres table (D2). A dedupe key stops the same job being queued twice.
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("dedupe_key", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name="jobs_status"),
    )
    op.create_index("jobs_one_pending", "jobs", ["dedupe_key"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_index("jobs_queued", "jobs", ["id"], postgresql_where=sa.text("status = 'queued'"))

    # Scorecard roll-ups: the analytics store's stand-in (D11). Derived data, recomputed whole.
    op.create_table(
        "scorecard_rows",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("metric_version", sa.Text, nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("worker_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submissions", sa.Integer, nullable=False),
        sa.Column("gold_n", sa.Integer, nullable=False),
        sa.Column("gold_correct", sa.Integer, nullable=False),
        sa.Column("compared", sa.Integer, nullable=False),
        sa.Column("agreed", sa.Integer, nullable=False),
        sa.Column("reviewed", sa.Integer, nullable=False),
        sa.Column("rejected", sa.Integer, nullable=False),
        sa.Column("accepted", sa.Integer, nullable=False),
        sa.Column("time_on_task_ms", sa.BigInteger, nullable=False),
        sa.Column("turnaround_s", sa.BigInteger, nullable=False),
        sa.Column("cost", sa.Numeric(14, 4), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("scorecard_rows_lookup", "scorecard_rows", ["metric_version", "project_id", "vendor_id", "period_start"])


def downgrade() -> None:
    op.drop_table("scorecard_rows")
    op.drop_table("jobs")
