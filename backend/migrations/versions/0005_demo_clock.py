"""Demo clock anchor

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One row, written by the seed when the demo clock is on. Every process reads it, so the
    # API and the background worker agree on simulated time.
    op.create_table(
        "demo_clock",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("real_epoch", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sim_epoch", sa.DateTime(timezone=True), nullable=False),
        sa.Column("speed", sa.Integer, nullable=False),
        sa.CheckConstraint("id = 1", name="demo_clock_single_row"),
        sa.CheckConstraint("speed >= 1", name="demo_clock_speed"),
    )


def downgrade() -> None:
    op.drop_table("demo_clock")
