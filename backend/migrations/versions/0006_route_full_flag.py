"""Mark full task routes so claims skip finished work

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A route is full when its task has as many live answers at that vendor as the project's
    # overlap. Claims skip full routes; the authoritative recount still runs after locking.
    op.add_column("task_routes", sa.Column("is_full", sa.Boolean, nullable=False, server_default=sa.false()))
    op.create_index("task_routes_vendor_open", "task_routes", ["vendor_id"], postgresql_where=sa.text("NOT is_full"))
    op.create_index("assignments_open_expiry", "assignments", ["expires_at"], postgresql_where=sa.text("status = 'open'"))


def downgrade() -> None:
    op.drop_index("assignments_open_expiry")
    op.drop_index("task_routes_vendor_open")
    op.drop_column("task_routes", "is_full")
