"""Drop what the schedule-run recovery sweep used.

``workflow.schedule_runs_recover`` is gone: an agent or workflow outcome now
reaches its schedule run as a fact written in the run's finishing transaction.
Its cursor column and the index that served its ordering have no reader left.

The index is dropped concurrently, so the table is never locked for writes.

Revision ID: 0065_drop_schedule_run_recovery
Revises: 0064_work_lease_epoch
"""

import sqlalchemy as sa
from alembic import op

revision = "0065_drop_schedule_run_recovery"
down_revision = "0064_work_lease_epoch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_schedule_runs_recoverable",
            table_name="schedule_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_column("schedule_runs", "last_inspected_at")


def downgrade() -> None:
    op.add_column(
        "schedule_runs",
        sa.Column("last_inspected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_schedule_runs_recoverable",
        "schedule_runs",
        [sa.text("last_inspected_at ASC NULLS FIRST"), "id"],
        postgresql_where=sa.text("target_outcome IS NULL"),
    )
