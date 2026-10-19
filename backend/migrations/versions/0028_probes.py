"""Probe solutions: a fourth sandbox check, run when a version carries known-wrong answers (D47)

Revision ID: 0028
Revises: 0027
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("validation_runs_check", "validation_runs")
    op.create_check_constraint("validation_runs_check", "validation_runs", "check_name IN ('reference', 'empty', 'repeat', 'probes')")


def downgrade() -> None:
    op.execute("DELETE FROM validation_runs WHERE check_name = 'probes'")
    op.drop_constraint("validation_runs_check", "validation_runs")
    op.create_check_constraint("validation_runs_check", "validation_runs", "check_name IN ('reference', 'empty', 'repeat')")
