"""Keep each limit window's spend as a running total.

`usage_limit_counters.basis` is the digest of which ledger rows a counter's
`used_usd` is the total of, and `reconciled_at` is when it was last recounted
from the ledger. Until now every model request recounted every window it fell
into; from here the counter is trusted while its basis matches the window
asking, and recounted only when it does not, when its limit changes, or when it
is older than `USAGE_COUNTER_REFRESH_SECONDS`.

Both columns start null, so every existing counter is recounted from the ledger
the first time a request uses it, exactly as it was before this revision. No
row is rewritten here: the table holds one row per limited window, and adding
nullable columns is a catalog change.

The downgrade drops both. Code from before this revision ignores them and
recounts on every request, so it is correct against the counters either way.

Revision ID: 0062_usage_counter_basis
Revises: 0061_answers_shared
"""

import sqlalchemy as sa
from alembic import op

revision = "0062_usage_counter_basis"
down_revision = "0061_answers_shared"
branch_labels = None
depends_on = None

TABLE = "usage_limit_counters"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("basis", sa.String(64), nullable=True))
    op.add_column(
        TABLE, sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column(TABLE, "reconciled_at")
    op.drop_column(TABLE, "basis")
