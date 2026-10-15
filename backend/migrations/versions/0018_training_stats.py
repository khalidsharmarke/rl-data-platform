"""Training statistics per run, environment version and step bucket (TO2, TO4, D39, B7)

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Sums, not averages, so new episodes can be added in without re-reading old ones. Stands in
    # for the analytics store (B7).
    op.create_table(
        "training_stats",
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("training_runs.id"), primary_key=True),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("environment_versions.id"), primary_key=True),
        sa.Column("step_bucket", sa.Integer, primary_key=True),
        sa.Column("episodes", sa.Integer, nullable=False),
        sa.Column("passed", sa.Integer, nullable=False),
        sa.Column("reward_sum", sa.Float, nullable=False),
        sa.Column("reward_sq_sum", sa.Float, nullable=False),
        sa.Column("length_sum", sa.BigInteger, nullable=False),
    )
    # The last episode sequence number folded into training_stats. Moves in the same transaction
    # as the sums, so a processor run that's repeated or retried never counts an episode twice.
    op.create_table(
        "training_stats_progress",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("through_seq", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="training_stats_progress_single_row"),
    )
    op.execute("INSERT INTO training_stats_progress (id, through_seq, updated_at) VALUES (1, 0, now())")


def downgrade() -> None:
    op.drop_table("training_stats_progress")
    op.drop_table("training_stats")
