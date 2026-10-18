"""Self-serve onboarding: invites, onboarding projects that feed nothing, and when each vendor
finished its checklist (VM1, VM3, N14, D46)

Revision ID: 0027
Revises: 0026
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Onboarding samples are allocated under an unpaid contract, so a price can be zero.
    op.drop_constraint("contracts_price_positive", "contracts")
    op.create_check_constraint("contracts_price_not_negative", "contracts", "price >= 0")
    op.add_column("projects", sa.Column("onboarding", sa.Boolean, nullable=False, server_default=sa.false()))
    # Real work is allocated only once this is set. Vendors that predate onboarding count as onboarded.
    op.add_column("orgs", sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE orgs SET onboarded_at = created_at WHERE kind = 'vendor'")
    op.create_table(
        "invites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("code_hash", sa.Text, nullable=False, unique=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("invites")
    op.drop_column("orgs", "onboarded_at")
    op.drop_column("projects", "onboarding")
    op.drop_constraint("contracts_price_not_negative", "contracts")
    op.create_check_constraint("contracts_price_positive", "contracts", "price > 0")
