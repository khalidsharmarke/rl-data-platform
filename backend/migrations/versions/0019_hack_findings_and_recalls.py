"""Reward-hack findings, the review queue for weak ones, recalls and training's reports
(TO3, TO6, TO8, D35, D37, D38)

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def uuid_pk() -> sa.Column:
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def upgrade() -> None:
    op.create_table(
        "hack_findings",
        uuid_pk(),
        sa.Column("detector", sa.Text, nullable=False),
        sa.Column("rule", sa.Text, nullable=False),
        sa.Column("rule_version", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("environment_versions.id"), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("training_runs.id"), nullable=False),
        sa.Column("observation", sa.Text, nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("through_seq", sa.BigInteger, nullable=False),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("severity IN ('watch', 'case')", name="hack_findings_severity"),
        sa.UniqueConstraint("detector", "version_id", "run_id", "rule", "severity", name="hack_findings_once"),
    )
    # A weak finding asks a researcher to read some transcripts (D38). The answer is recorded beside it.
    op.create_table(
        "hack_checks",
        uuid_pk(),
        sa.Column("finding_id", UUID(as_uuid=True), sa.ForeignKey("hack_findings.id"), nullable=False, unique=True),
        sa.Column("outcome", sa.Text, nullable=True),
        sa.Column("checked_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("outcome IS NULL OR outcome IN ('looks_fine', 'flagged')", name="hack_checks_outcome"),
    )
    op.create_table(
        "hack_detection_progress",
        sa.Column("detector", sa.Text, primary_key=True),
        sa.Column("through_seq", sa.BigInteger, nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
    )
    # The recall (TO8, D37): what training needs to roll back, issued when a version it used is quarantined.
    op.create_table(
        "recalls",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id"), nullable=False, unique=True),
        sa.Column("version_id", UUID(as_uuid=True), sa.ForeignKey("environment_versions.id"), nullable=False),
        sa.Column("runs", JSONB, nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "recall_reports",
        uuid_pk(),
        sa.Column("recall_id", sa.BigInteger, sa.ForeignKey("recalls.id"), nullable=False),
        sa.Column("run_label", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("checkpoint_step", sa.Integer, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action IN ('rolled_back', 'continued')", name="recall_reports_action"),
        sa.UniqueConstraint("recall_id", "run_label", name="recall_reports_once_per_run"),
    )
    for table in ("hack_findings", "recalls", "recall_reports"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_change()")
    # Case history gains the recall and training's reports.
    op.drop_constraint("case_actions_action", "case_actions")
    op.create_check_constraint(
        "case_actions_action", "case_actions",
        "action IN ('opened', 'hold', 'release', 'clear', 'warn', 'suspend', 'remove', 'reverse', 'cleanup', 'recall', 'report', 'flag')",
    )


def downgrade() -> None:
    op.drop_constraint("case_actions_action", "case_actions")
    op.create_check_constraint(
        "case_actions_action", "case_actions",
        "action IN ('opened', 'hold', 'release', 'clear', 'warn', 'suspend', 'remove', 'reverse', 'cleanup')",
    )
    for t in ("recall_reports", "recalls", "hack_detection_progress", "hack_checks", "hack_findings"):
        op.drop_table(t)
