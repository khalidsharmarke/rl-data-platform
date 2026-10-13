"""Abuse: flags, cases, holds, case actions, worker suspension

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def uuid_pk() -> sa.Column:
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def upgrade() -> None:
    op.add_column("users", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))

    # One flow for both levels (D13): flags open cases; the owner follows the subject.
    op.create_table(
        "cases",
        uuid_pk(),
        sa.Column("subject_type", sa.Text, nullable=False),
        sa.Column("subject_id", UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("owner", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision", sa.Text, nullable=True),
        sa.Column("decision_reason", sa.Text, nullable=True),
        sa.Column("decided_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("subject_type IN ('worker', 'vendor', 'environment_version')", name="cases_subject_type"),
        sa.CheckConstraint("owner IN ('vendor_admin', 'data_ops')", name="cases_owner"),
        sa.CheckConstraint("status IN ('open', 'decided')", name="cases_status"),
        sa.CheckConstraint("decision IS NULL OR decision IN ('clear', 'warn', 'suspend', 'remove')", name="cases_decision"),
    )
    op.create_index("cases_one_open_per_subject", "cases", ["subject_type", "subject_id"], unique=True,
                    postgresql_where=sa.text("status = 'open'"))
    op.create_index("cases_vendor", "cases", ["vendor_id", "opened_at"])

    op.create_table(
        "flags",
        uuid_pk(),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("rule", sa.Text, nullable=False),
        sa.Column("rule_version", sa.Text, nullable=False),
        sa.Column("observation", sa.Text, nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_id", "rule", name="flags_once_per_rule_per_case"),
    )

    # Automatic, reversible protection while a case is open (AB8).
    op.create_table(
        "holds",
        uuid_pk(),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("worker_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("holds_active_worker", "holds", ["worker_id"], postgresql_where=sa.text("released_at IS NULL"))

    # Every action, by a person or the platform, is tied to its case (N18). Never edited.
    op.create_table(
        "case_actions",
        uuid_pk(),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('opened', 'hold', 'release', 'clear', 'warn', 'suspend', 'remove', 'reverse', 'cleanup')",
            name="case_actions_action",
        ),
    )
    op.execute("CREATE TRIGGER case_actions_immutable BEFORE UPDATE OR DELETE ON case_actions FOR EACH ROW EXECUTE FUNCTION reject_change()")
    op.execute("CREATE TRIGGER flags_immutable BEFORE UPDATE OR DELETE ON flags FOR EACH ROW EXECUTE FUNCTION reject_change()")


def downgrade() -> None:
    for t in ("case_actions", "holds", "flags", "cases"):
        op.drop_table(t)
    op.drop_column("users", "suspended_at")
