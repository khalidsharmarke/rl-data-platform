"""The code task generator records the families it stops, as the token it signs in with (D48, D49)

Revision ID: 0030
Revises: 0029
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("family_stops", "stopped_by", nullable=True)
    op.add_column("family_stops", sa.Column("stopped_by_token", UUID(as_uuid=True), sa.ForeignKey("api_tokens.id"), nullable=True))
    op.create_check_constraint("family_stops_stopper", "family_stops", "(stopped_by IS NULL) <> (stopped_by_token IS NULL)")


def downgrade() -> None:
    op.execute("DELETE FROM family_stops WHERE stopped_by IS NULL")
    op.drop_constraint("family_stops_stopper", "family_stops")
    op.drop_column("family_stops", "stopped_by_token")
    op.alter_column("family_stops", "stopped_by", nullable=False)
