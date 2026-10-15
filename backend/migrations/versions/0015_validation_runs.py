"""Validation runs: one row per check on an environment version (EC3, D4)

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # When a job was taken, so fair share can count what each vendor has already been served (D4).
    op.add_column("jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "validation_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("environment_versions.id"), nullable=False),
        sa.Column("check_name", sa.Text, nullable=False),
        # passed: the check's expectation held (the reference passed, the empty solution failed, the repeats agreed).
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("detail", sa.Text, nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        # Combined output of both steps, stored by hash like everything else (D34).
        sa.Column("logs_hash", sa.Text, sa.ForeignKey("content_objects.hash"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("check_name IN ('reference', 'empty', 'repeat')", name="validation_runs_check"),
        sa.CheckConstraint("outcome IN ('pass', 'fail', 'timeout', 'error')", name="validation_runs_outcome"),
    )
    op.execute("CREATE TRIGGER validation_runs_immutable BEFORE UPDATE OR DELETE ON validation_runs "
               "FOR EACH ROW EXECUTE FUNCTION reject_change()")


def downgrade() -> None:
    op.drop_table("validation_runs")
    op.drop_column("jobs", "started_at")
