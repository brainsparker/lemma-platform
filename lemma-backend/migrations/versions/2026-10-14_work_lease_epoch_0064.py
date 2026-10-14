"""A fencing token for each claim of a work item.

``lease_owner`` names a process and class, not a claim. When a lease expired
under a process that was still running the job (its heartbeat could not reach
the database) and the same process claimed the item again, the old run and the
new one both matched ``lease_owner = :me``: both could write their steps and
either could complete the item. ``lease_epoch`` goes up by one on every claim,
and every fenced statement matches it too, so only the latest claim can write.

Adding a column with a constant default rewrites nothing.

Revision ID: 0064_work_lease_epoch
Revises: 0063_drop_event_outbox
"""

import sqlalchemy as sa
from alembic import op

revision = "0064_work_lease_epoch"
down_revision = "0063_drop_event_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column(
            "lease_epoch", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    op.drop_column("work_items", "lease_epoch")
