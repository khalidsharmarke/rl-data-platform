"""Orgs and users, enough for demo logins

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orgs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('internal', 'vendor')", name="orgs_kind"),
    )
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('researcher', 'data_ops', 'vendor_admin', 'vendor_reviewer', 'worker', 'environment_builder')",
            name="users_role",
        ),
    )
    op.create_index("users_org_id", "users", ["org_id"])


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("orgs")
