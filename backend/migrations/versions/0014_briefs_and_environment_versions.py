"""Environment briefs (a project kind), environments, immutable versions, content by hash

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # D33: one project model, two kinds. A brief's request lives in `brief`.
    op.add_column("projects", sa.Column("kind", sa.Text, nullable=False, server_default="human_data"))
    op.add_column("projects", sa.Column("brief", JSONB, nullable=True))
    op.create_check_constraint("projects_kind", "projects", "kind IN ('human_data', 'environment')")
    op.create_check_constraint("projects_brief_for_environments", "projects", "(kind = 'environment') = (brief IS NOT NULL)")

    op.create_table(
        "environments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "org_id", "name", name="environments_unique_name"),
    )

    # D34: every stored file once, named by its SHA-256. Never edited or deleted here.
    op.create_table(
        "content_objects",
        sa.Column("hash", sa.Text, primary_key=True),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("CREATE TRIGGER content_objects_immutable BEFORE UPDATE OR DELETE ON content_objects "
               "FOR EACH ROW EXECUTE FUNCTION reject_change()")

    # D3: a version's manifest never changes; only its status moves, through the state machine
    # in app/environments.py. A version can be deleted only if it never got past validation (N22).
    op.create_table(
        "environment_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("environment_id", UUID(as_uuid=True), sa.ForeignKey("environments.id"), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("manifest", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("environment_id", "number", name="environment_versions_number"),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'validating', 'failed', 'in_review', 'approved', 'quarantined')",
            name="environment_versions_status",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION environment_version_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status NOT IN ('draft', 'failed') THEN
                    RAISE EXCEPTION 'environment version % passed validation and is kept for good', OLD.id;
                END IF;
                RETURN OLD;
            END IF;
            IF NEW.id <> OLD.id OR NEW.environment_id <> OLD.environment_id OR NEW.number <> OLD.number
               OR NEW.manifest <> OLD.manifest OR NEW.created_by <> OLD.created_by OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'environment versions are immutable; only their status changes';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("CREATE TRIGGER environment_versions_guard BEFORE UPDATE OR DELETE ON environment_versions "
               "FOR EACH ROW EXECUTE FUNCTION environment_version_guard()")


def downgrade() -> None:
    op.drop_table("environment_versions")
    op.execute("DROP FUNCTION environment_version_guard()")
    op.drop_table("content_objects")
    op.drop_table("environments")
    op.drop_constraint("projects_brief_for_environments", "projects")
    op.drop_constraint("projects_kind", "projects")
    op.drop_column("projects", "brief")
    op.drop_column("projects", "kind")
