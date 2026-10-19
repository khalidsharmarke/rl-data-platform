"""Extra overlap for watched workers: a task a watched worker answered asks its vendor for one more answer (AB14, D52)

Revision ID: 0032
Revises: 0031
"""
from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_routes", sa.Column("extra_overlap", sa.Integer, nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("task_routes", "extra_overlap")
