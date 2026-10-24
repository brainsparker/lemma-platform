"""A shared pod account goes inside a sandbox only if its owner says so.

The sandbox's git credential bridge writes a GitHub token into a workspace,
where every later command can read it. An account shared with a pod was shared
for operations run through Lemma, not for a shell to hold, so the bridge
refuses one that is not the acting person's own -- unless its owner ticked
"allow use inside sandboxes": `pod_accounts.allow_sandbox_use`, set only
through the owner's own PATCH and cleared when they withdraw the account.

Every existing row starts false, which is the behaviour before the column
existed: a constant default is a catalog change, so no row is rewritten. The
downgrade drops it; code from before this revision never reads it.

``lock_timeout`` makes a migration that cannot get the table's lock fail and be
retried, rather than queue every connector call behind it.

Revision ID: 0064_pod_account_sandbox_use
Revises: 0063_pod_connectors
"""

import sqlalchemy as sa
from alembic import op

revision = "0064_pod_account_sandbox_use"
down_revision = "0063_pod_connectors"
branch_labels = None
depends_on = None

TABLE = "pod_accounts"
COLUMN = "allow_sandbox_use"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default="false"),
    )
    op.execute("SET LOCAL lock_timeout TO DEFAULT")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.drop_column(TABLE, COLUMN)
    op.execute("SET LOCAL lock_timeout TO DEFAULT")
