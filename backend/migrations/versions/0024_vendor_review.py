"""Vendor review: per allocation, a vendor's reviewers accept or correct answers before they
reach Anthropic; the worker's original is never overwritten (DC8, D44, A29)

Revision ID: 0024
Revises: 0023
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("allocations", sa.Column("vendor_review", sa.Boolean, nullable=False, server_default=sa.false()))
    # One review per answer. A correction is a second version of the answer: submissions.answer
    # stays the worker's own, which detection, gold and grades judge.
    op.create_table(
        "vendor_reviews",
        sa.Column("submission_id", UUID(as_uuid=True), sa.ForeignKey("submissions.id"), primary_key=True),
        sa.Column("reviewer_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.Text, nullable=False),
        sa.Column("corrected_answer", JSONB, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('accepted', 'corrected')", name="vendor_reviews_decision"),
        sa.CheckConstraint("(decision = 'corrected') = (corrected_answer IS NOT NULL)", name="vendor_reviews_correction"),
    )
    op.execute("CREATE TRIGGER vendor_reviews_immutable BEFORE UPDATE OR DELETE ON vendor_reviews "
               "FOR EACH ROW EXECUTE FUNCTION reject_change()")


def downgrade() -> None:
    op.drop_table("vendor_reviews")
    op.drop_column("allocations", "vendor_review")
