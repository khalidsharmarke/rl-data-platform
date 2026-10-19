"""API signals: the session and device each claim and submission came from (AB12, D51)

Revision ID: 0031
Revises: 0030
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The session is the platform's (one per sign-in); the device id is the browser's own, so untrusted (AB11).
    for table in ("assignments", "submissions"):
        op.add_column(table, sa.Column("session_id", UUID(as_uuid=True), nullable=True))
        op.add_column(table, sa.Column("device_id", sa.Text, nullable=True))
    op.create_index("assignments_worker_assigned", "assignments", ["worker_id", "assigned_at"])


def downgrade() -> None:
    op.drop_index("assignments_worker_assigned")
    for table in ("assignments", "submissions"):
        op.drop_column(table, "device_id")
        op.drop_column(table, "session_id")
