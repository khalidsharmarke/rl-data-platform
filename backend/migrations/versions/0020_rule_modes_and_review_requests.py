"""Rule modes, abuse judgements on spot checks, the review requests that feed the spot-check
queue, and detection outcomes (AB15, AB16, D27, D28, D40, D41)

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

# Rule versions that were acting before modes existed: they stay live.
LIVE = [("rules-v1", "speed-v1"), ("rules-v1", "paste-v1"), ("rules-v1", "duplicate-v1"),
        ("hacks-v1", "shortcut-v1"), ("hacks-v1", "touched-grader-v1"), ("hacks-v1", "jump-v1")]


def upgrade() -> None:
    # The platform's record of each rule version's mode (D41). A version not listed is in shadow.
    op.create_table(
        "rule_modes",
        sa.Column("rule_version", sa.Text, primary_key=True),
        sa.Column("detector", sa.Text, nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("bar", sa.Numeric(4, 3), nullable=True),  # the precision data ops asks of it
        sa.Column("changed_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('shadow', 'live')", name="rule_modes_mode"),
    )
    for detector, version in LIVE:
        op.execute(f"INSERT INTO rule_modes (rule_version, detector, mode, changed_at) VALUES ('{version}', '{detector}', 'live', now())")
    # Set when the finding is stored (findings never change): a shadow finding has no response.
    op.add_column("findings", sa.Column("shadow", sa.Boolean, nullable=False, server_default=sa.false()))
    op.add_column("hack_findings", sa.Column("shadow", sa.Boolean, nullable=False, server_default=sa.false()))

    # Whether the answer looks like abuse, asked on every spot check (D40). Null on reviews from before.
    op.add_column("reviews", sa.Column("looks_abusive", sa.Boolean, nullable=True))

    # Answers the platform wants a person to look at. Reviewers never see why an item is there.
    op.create_table(
        "review_requests",
        sa.Column("submission_id", UUID(as_uuid=True), sa.ForeignKey("submissions.id"), primary_key=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("finding_id", UUID(as_uuid=True), sa.ForeignKey("findings.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reason IN ('audit', 'finding')", name="review_requests_reason"),
    )

    # What people concluded, for the detection service: abuse judgements and escalation outcomes.
    # Case decisions keep their own feed (/detection/labels).
    op.create_table(
        "detection_outcomes",
        sa.Column("seq", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("worker_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("submission_id", UUID(as_uuid=True), sa.ForeignKey("submissions.id"), nullable=True),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("through_event", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('judgement', 'escalation')", name="detection_outcomes_kind"),
        sa.CheckConstraint("outcome IN ('abusive', 'not_abusive', 'cleared')", name="detection_outcomes_outcome"),
    )


def downgrade() -> None:
    op.drop_table("detection_outcomes")
    op.drop_table("review_requests")
    op.drop_column("reviews", "looks_abusive")
    op.drop_column("hack_findings", "shadow")
    op.drop_column("findings", "shadow")
    op.drop_table("rule_modes")
