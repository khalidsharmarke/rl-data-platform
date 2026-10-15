"""Researcher reviews of environment versions, and catalog pulls by training (EC5, EC6, N22)

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "environment_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("environment_versions.id"), nullable=False),
        sa.Column("reviewer_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        # One entry per criterion in the brief's rubric: met or not (EC5).
        sa.Column("rubric", JSONB, nullable=False),
        sa.Column("feedback", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('approve', 'send_back')", name="environment_reviews_decision"),
    )
    # Who received which version, kept with the version for lineage (N22).
    op.create_table(
        "catalog_pulls",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("environment_versions.id"), nullable=False),
        sa.Column("puller", sa.Text, nullable=False),
        sa.Column("run_label", sa.Text, nullable=True),
        sa.Column("pulled_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("catalog_pulls_version", "catalog_pulls", ["version_id"])
    for table in ("environment_reviews", "catalog_pulls"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_change()")


def downgrade() -> None:
    op.drop_table("catalog_pulls")
    op.drop_table("environment_reviews")
