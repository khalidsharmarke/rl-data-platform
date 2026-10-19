"""The cross-vendor match and vendor-private rules (AB2, AB9, AB10, D55, D56)

Revision ID: 0033
Revises: 0032
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Only a keyed hash of each worker's stable identifier, never the identifier (D55).
    op.create_table(
        "worker_identities",
        sa.Column("worker_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("hash", sa.Text, nullable=False),
        sa.Column("key_version", sa.Integer, nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("hash ~ '^[0-9a-f]{64}$'", name="worker_identities_hash"),
    )
    op.create_index("worker_identities_by_hash", "worker_identities", ["hash"])
    # A vendor's own rules: thresholds on signals the platform records (D56).
    op.create_table(
        "vendor_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("signal", sa.Text, nullable=False),
        sa.Column("threshold", sa.Numeric(12, 3), nullable=False),
        sa.Column("window", sa.Integer, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("signal IN ('fast', 'pasted', 'focus_changes', 'quick_start')", name="vendor_rules_signal"),
        sa.CheckConstraint("\"window\" BETWEEN 5 AND 500", name="vendor_rules_window"),
    )
    # A private case is the vendor's alone: never listed for Anthropic, never a hold, never a label.
    op.add_column("cases", sa.Column("private", sa.Boolean, nullable=False, server_default=sa.false()))
    op.drop_index("cases_one_open_per_subject", "cases")
    op.create_index("cases_one_open_per_subject", "cases", ["subject_type", "subject_id", "private"], unique=True,
                    postgresql_where=sa.text("status = 'open'"))


def downgrade() -> None:
    op.drop_index("cases_one_open_per_subject", "cases")
    op.execute("DELETE FROM flags WHERE case_id IN (SELECT id FROM cases WHERE private)")
    op.execute("DELETE FROM case_actions WHERE case_id IN (SELECT id FROM cases WHERE private)")
    op.execute("DELETE FROM cases WHERE private")
    op.create_index("cases_one_open_per_subject", "cases", ["subject_type", "subject_id"], unique=True,
                    postgresql_where=sa.text("status = 'open'"))
    op.drop_column("cases", "private")
    op.drop_table("vendor_rules")
    op.drop_table("worker_identities")
