"""Discreet escalation: closer measurement of a worker after a watch finding (AB14, D26, D32)

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "escalations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("worker_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("finding_id", UUID(as_uuid=True), sa.ForeignKey("findings.id"), nullable=False),
        # Gold answered after this event counts toward clearing or confirming the finding.
        sa.Column("after_event", sa.BigInteger, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text, nullable=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id"), nullable=True),
        sa.CheckConstraint("outcome IS NULL OR outcome IN ('cleared', 'confirmed', 'case')", name="escalations_outcome"),
    )
    op.create_index("escalations_one_open", "escalations", ["worker_id", "project_id"], unique=True,
                    postgresql_where=sa.text("ended_at IS NULL"))


def downgrade() -> None:
    op.drop_table("escalations")
