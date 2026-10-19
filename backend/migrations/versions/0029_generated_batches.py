"""Generated tasks reviewed by sample, and task families stopped on a send-back (EC7, D49)

Revision ID: 0029
Revises: 0028
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A batch is the generator's word for one round of generation; the versions name it in their
    # manifests. It becomes a row once the generator says it's complete and the sample is drawn.
    op.create_table(
        "generated_batches",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("batch", sa.Text, primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('in_review', 'approved', 'sent_back', 'nothing_passed')", name="generated_batches_status"),
    )
    op.create_table(
        "batch_samples",
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("environment_versions.id"), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("batch", sa.Text, nullable=False),
        sa.ForeignKeyConstraint(["project_id", "batch"], ["generated_batches.project_id", "generated_batches.batch"]),
    )
    op.create_table(
        "family_stops",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("family", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("stopped_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resumed_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("family_stops_active", "family_stops", ["org_id", "family"], unique=True, postgresql_where=sa.text("resumed_at IS NULL"))


def downgrade() -> None:
    op.drop_table("family_stops")
    op.drop_table("batch_samples")
    op.drop_table("generated_batches")
