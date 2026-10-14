"""Detection contract: submission events (outbox), detection progress, findings

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One event per submission, written in the submission's transaction (D24). The detection
    # service reads them in id order through the API; ids are commit-ordered (see app/events.py).
    op.create_table(
        "submission_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("submission_id", UUID(as_uuid=True), sa.ForeignKey("submissions.id"), nullable=False, unique=True),
        sa.Column("worker_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("CREATE TRIGGER submission_events_immutable BEFORE UPDATE OR DELETE ON submission_events "
               "FOR EACH ROW EXECUTE FUNCTION reject_change()")
    # Submissions made before events existed get theirs now, in submission order.
    op.execute(
        """
        INSERT INTO submission_events (submission_id, worker_id, project_id, payload, created_at)
        SELECT s.id, s.worker_id, s.project_id,
               jsonb_build_object(
                 'submission_id', s.id, 'worker_id', s.worker_id, 'vendor_id', s.vendor_id, 'project_id', s.project_id,
                 'task_id', s.task_id, 'submitted_at', s.submitted_at, 'time_on_task_ms', s.time_on_task_ms,
                 'task_words', coalesce(array_length(regexp_split_to_array(trim(
                     coalesce(t.payload->>'prompt', '') || ' ' || coalesce(t.payload->>'response_a', '') || ' ' ||
                     coalesce(t.payload->>'response_b', '')), '\\s+'), 1), 0),
                 'answer', s.answer, 'client_signals', coalesce(s.client_signals, '{}'::jsonb)),
               s.submitted_at
        FROM submissions s JOIN tasks t ON t.id = s.task_id
        ORDER BY s.submitted_at, s.id
        """
    )

    # How far each detector has processed the event stream (D25).
    op.create_table(
        "detection_progress",
        sa.Column("detector", sa.Text, primary_key=True),
        sa.Column("processed_through", sa.BigInteger, nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Everything a detector reports, whatever it leads to (AB13, D32). Never edited.
    op.create_table(
        "findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("detector", sa.Text, nullable=False),
        sa.Column("rule", sa.Text, nullable=False),
        sa.Column("rule_version", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("worker_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), sa.ForeignKey("orgs.id"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("observation", sa.Text, nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("through_event", sa.BigInteger, nullable=False),
        sa.Column("case_id", UUID(as_uuid=True), sa.ForeignKey("cases.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("severity IN ('watch', 'case')", name="findings_severity"),
        # Delivery is at least once: a detector that restarts may report the same finding again.
        sa.UniqueConstraint("detector", "worker_id", "project_id", "rule", "severity", "through_event", name="findings_once"),
    )
    op.create_index("findings_worker", "findings", ["worker_id", "created_at"])
    op.execute("CREATE TRIGGER findings_immutable BEFORE UPDATE OR DELETE ON findings FOR EACH ROW EXECUTE FUNCTION reject_change()")


def downgrade() -> None:
    for t in ("findings", "detection_progress", "submission_events"):
        op.drop_table(t)
