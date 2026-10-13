"""Quality: project quality bar, researcher reviews, gold rotation

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The quality bar (DC1): minimum gold accuracy for a worker's answers to be accepted,
    # applied once the worker has at least min_gold_results gold answers.
    op.add_column("projects", sa.Column("quality_bar", sa.Numeric(4, 3), nullable=False, server_default="0.800"))
    op.add_column("projects", sa.Column("min_gold_results", sa.Integer, nullable=False, server_default="5"))

    # Researcher spot checks (DC4). Reviews are never edited; the latest one for a submission counts.
    op.create_table(
        "reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("submission_id", UUID(as_uuid=True), sa.ForeignKey("submissions.id"), nullable=False),
        sa.Column("reviewer_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("verdict", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("verdict IN ('accepted', 'rejected')", name="reviews_verdict"),
        sa.CheckConstraint("verdict = 'accepted' OR length(trim(reason)) > 0", name="reviews_rejection_has_reason"),
    )
    op.create_index("reviews_submission", "reviews", ["submission_id", "created_at"])
    op.execute(
        "CREATE TRIGGER reviews_immutable BEFORE UPDATE OR DELETE ON reviews FOR EACH ROW EXECUTE FUNCTION reject_change()"
    )

    # Gold rotation (PD2): a retired gold task is no longer handed out. Its past answers still count.
    op.create_table(
        "gold_retirements",
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("gold_answers.task_id"), primary_key=True),
        sa.Column("retired_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("gold_retirements")
    op.drop_table("reviews")
    op.drop_column("projects", "min_gold_results")
    op.drop_column("projects", "quality_bar")
