"""Vendor cases: shared with the vendor by data ops, the vendor's responses, and actions on
vendors (AB2, AB5, AB7, D13, D42)

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

BEFORE = "'opened', 'hold', 'release', 'clear', 'warn', 'suspend', 'remove', 'reverse', 'cleanup', 'recall', 'report', 'flag'"
AFTER = BEFORE + ", 'shared', 'response', 'lower_allocation', 'pause', 'offboard', 'resolve'"


def upgrade() -> None:
    # A vendor case stays inside Anthropic until data ops asks the vendor to respond; the response
    # window runs from then.
    op.add_column("cases", sa.Column("shared_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orgs", sa.Column("offboarded_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("case_actions_action", "case_actions")
    op.create_check_constraint("case_actions_action", "case_actions", f"action IN ({AFTER})")
    # A vendor case ends cleared, or resolved after an action on the vendor.
    op.drop_constraint("cases_decision", "cases")
    op.create_check_constraint("cases_decision", "cases", "decision IS NULL OR decision IN ('clear', 'warn', 'suspend', 'remove', 'resolve')")


def downgrade() -> None:
    op.drop_constraint("cases_decision", "cases")
    op.create_check_constraint("cases_decision", "cases", "decision IS NULL OR decision IN ('clear', 'warn', 'suspend', 'remove')")
    op.drop_constraint("case_actions_action", "case_actions")
    op.create_check_constraint("case_actions_action", "case_actions", f"action IN ({BEFORE})")
    op.drop_column("orgs", "offboarded_at")
    op.drop_column("cases", "shared_at")
