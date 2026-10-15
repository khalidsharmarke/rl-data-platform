"""The upload each task came in, so a batch with low agreement can be found (DC5, D43)

Revision ID: 0023
Revises: 0022
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tasks never change, so rows from before this have none; they're grouped by created_at instead.
    op.add_column("tasks", sa.Column("batch_id", UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "batch_id")
