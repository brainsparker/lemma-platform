"""A group shows its agents' answers to everybody in it, unless it keeps them
to the asker.

`channels.answers_shared`: when an agent answers a member in a group with
that member's own access, the answer is posted shared (`channel_messages.
shared_at` set as it is written), so the others in the group read it in
full. Off, it is today's rule: read in full by the asker alone, the others
shown that it was given, until the asker shares it. Only a GROUP's is read; a
DIRECT or CONTACT channel keeps the column at its default and ignores it.

Every existing channel takes the default, true: groups that kept answers to
the asker until now show the answers posted from now on to everybody. Lines
already written keep their `shared_at` as it is. Adding a column with a
constant default rewrites no row on Postgres 11+, so the pod's hottest table
is not rewritten.

`COLUMN` is the column's DDL exactly as the model compiles it, pinned to it by
``channels/tests/unit/test_channels_ddl_matches_models``.

Revision ID: 0061_answers_shared
Revises: 0060_slack_apps
"""

from alembic import op

revision = "0061_answers_shared"
down_revision = "0060_slack_apps"
branch_labels = None
depends_on = None

TABLE = "channels"
COLUMN = "answers_shared BOOLEAN DEFAULT true NOT NULL"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN}")


def downgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN answers_shared")
