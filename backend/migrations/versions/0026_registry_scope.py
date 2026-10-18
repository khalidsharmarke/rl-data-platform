"""The registry scope: a vendor token that can push and pull the vendor's own images (VM2, D50)

Revision ID: 0026
Revises: 0025
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("api_tokens_scopes", "api_tokens")
    op.create_check_constraint("api_tokens_scopes", "api_tokens",
                               "scopes <@ ARRAY['environments', 'read', 'registry']::text[] AND cardinality(scopes) > 0")


def downgrade() -> None:
    op.drop_constraint("api_tokens_scopes", "api_tokens")
    op.create_check_constraint("api_tokens_scopes", "api_tokens",
                               "scopes <@ ARRAY['environments', 'read']::text[] AND cardinality(scopes) > 0")
