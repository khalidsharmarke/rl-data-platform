"""Training runs and episodes reported by training (TO1, D5, D6, D36, B7)

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("label", sa.Text, nullable=False, unique=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    # One row per episode, keyed by training's own episode id so a resent batch is a no-op (D6).
    # In the design this is the durable log in front of the analytics store (D5); here it's a
    # Postgres table (build decision B7). Vendor is copied from the version for attribution (TO7).
    op.create_table(
        "episodes",
        sa.Column("episode_id", sa.Text, primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("training_runs.id"), nullable=False),
        sa.Column("step", sa.Integer, nullable=False),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("environment_versions.id"), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("reward", sa.Float, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("length", sa.Integer, nullable=False),
        sa.Column("touched", ARRAY(sa.Text), nullable=False),
        sa.Column("transcript_hash", sa.Text, sa.ForeignKey("content_objects.hash"), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False, unique=True),
        sa.CheckConstraint("step >= 0 AND length >= 0", name="episodes_sane"),
    )
    op.create_index("episodes_run_step", "episodes", ["run_id", "step"])
    op.create_index("episodes_version", "episodes", ["version_id", "step"])
    op.execute("CREATE TRIGGER episodes_immutable BEFORE UPDATE OR DELETE ON episodes FOR EACH ROW EXECUTE FUNCTION reject_change()")


def downgrade() -> None:
    op.drop_table("episodes")
    op.drop_table("training_runs")
