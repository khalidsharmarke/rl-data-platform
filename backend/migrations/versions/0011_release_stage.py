"""Release stage: per-project minimum gap and cap, and each exported row's tier

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PD7, DC10: how long an accepted answer waits at least, and at most before it's released as provisional.
    op.add_column("projects", sa.Column("release_after_minutes", sa.Integer, nullable=False, server_default="60"))
    op.add_column("projects", sa.Column("release_cap_minutes", sa.Integer, nullable=False, server_default="1440"))
    op.create_check_constraint("projects_release_window", "projects",
                               "release_after_minutes >= 0 AND release_cap_minutes >= release_after_minutes")
    # D31: verified or provisional. Rows exported before the release stage existed have none.
    op.add_column("dataset_rows", sa.Column("tier", sa.Text, nullable=True))
    op.create_check_constraint("dataset_rows_tier", "dataset_rows", "tier IS NULL OR tier IN ('verified', 'provisional')")


def downgrade() -> None:
    op.drop_constraint("dataset_rows_tier", "dataset_rows")
    op.drop_column("dataset_rows", "tier")
    op.drop_constraint("projects_release_window", "projects")
    op.drop_column("projects", "release_cap_minutes")
    op.drop_column("projects", "release_after_minutes")
