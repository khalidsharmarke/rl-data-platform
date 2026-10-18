"""API tokens: the vendor org's credentials for its own tooling, named, scoped, stored as a
hash and expiring; actions taken with one are recorded as the token's (VM2, N11, D45)

Revision ID: 0025
Revises: 0024
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("scopes", ARRAY(sa.Text), nullable=False),
        # The first characters of the secret, so a token can be recognized in a list or a leak report.
        sa.Column("prefix", sa.Text, nullable=False),
        sa.Column("secret_hash", sa.Text, nullable=False, unique=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.CheckConstraint("scopes <@ ARRAY['environments', 'read']::text[] AND cardinality(scopes) > 0", name="api_tokens_scopes"),
    )
    op.create_index("api_tokens_active_name", "api_tokens", ["org_id", "name"], unique=True,
                    postgresql_where=sa.text("revoked_at IS NULL"))
    op.add_column("audit_events", sa.Column("token_id", UUID(as_uuid=True), sa.ForeignKey("api_tokens.id"), nullable=True))
    # Environments and versions are created by a person or by one of the vendor's tokens, never both.
    for table in ("environments", "environment_versions"):
        op.alter_column(table, "created_by", nullable=True)
        op.add_column(table, sa.Column("created_by_token", UUID(as_uuid=True), sa.ForeignKey("api_tokens.id"), nullable=True))
        op.create_check_constraint(f"{table}_creator", table, "(created_by IS NULL) <> (created_by_token IS NULL)")


def downgrade() -> None:
    for table in ("environments", "environment_versions"):
        op.drop_constraint(f"{table}_creator", table)
        op.drop_column(table, "created_by_token")
        op.alter_column(table, "created_by", nullable=False)
    op.drop_column("audit_events", "token_id")
    op.drop_table("api_tokens")
