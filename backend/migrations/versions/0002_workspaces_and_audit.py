"""Vendor capabilities, worker handles and the append-only audit log

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("capabilities", ARRAY(sa.Text), nullable=False, server_default="{}"))
    op.add_column("orgs", sa.Column("domains", ARRAY(sa.Text), nullable=False, server_default="{}"))
    op.create_check_constraint(
        "orgs_capabilities", "orgs", "capabilities <@ ARRAY['human_work', 'environments']::text[]"
    )

    # An opaque, random handle: how people outside a worker's vendor see that worker (D12).
    op.add_column("users", sa.Column("handle", sa.Text, nullable=True))
    op.execute(
        "UPDATE users SET handle = 'U-' || upper(substr(md5(gen_random_uuid()::text), 1, 6)) WHERE handle IS NULL"
    )
    op.alter_column("users", "handle", nullable=False)
    op.create_unique_constraint("users_handle", "users", ["handle"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Text, nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=True),
        sa.Column("before", JSONB, nullable=True),
        sa.Column("after", JSONB, nullable=True),
    )
    op.create_index("audit_events_org_id_at", "audit_events", ["org_id", "at"])

    # Append-only (VM4): the database itself refuses to change or remove audit history.
    op.execute(
        """
        CREATE FUNCTION audit_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()"
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION audit_events_append_only()")
    op.drop_constraint("users_handle", "users")
    op.drop_column("users", "handle")
    op.drop_constraint("orgs_capabilities", "orgs")
    op.drop_column("orgs", "domains")
    op.drop_column("orgs", "capabilities")
