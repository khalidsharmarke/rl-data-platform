"""Contributor grades recorded on exported rows (D31)

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The worker's grades at release. A grade not recorded here can't be recovered later,
    # so researchers could never filter on it (DECISIONS.md: one-way).
    op.add_column("dataset_rows", sa.Column("grades", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("dataset_rows", "grades")
