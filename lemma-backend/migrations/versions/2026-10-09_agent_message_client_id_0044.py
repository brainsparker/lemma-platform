"""One stored message per send, however many times the client sends it.

A client that does not hear back from a send -- a dropped connection, a double
click, a retry after a timeout -- sends it again. Each copy used to become its
own message, and either a second run or a second ask steered into the first.
A send now carries a ``client_message_id``, stamped into the message's
metadata by the server, and a second send with the same id in the same
conversation returns the first one's run.

The check itself happens under the conversation lock, where the two copies are
serialised. This index is the backstop that holds even if a writer ever skips
the lock. Partial on the key being present, so messages written without one --
everything before this, and every message a chat platform or the agent writes
-- cost nothing in it.

Operator note: building the index holds a SHARE lock on ``agent_messages`` for
the length of one scan, which blocks message writes but not reads. On an
installation where that table is large, build it first, out of band, with

    CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
        uq_agent_message_client_message_id
        ON agent_messages (conversation_id, (message_metadata ->> 'client_message_id'))
        WHERE message_metadata ? 'client_message_id';

and this migration then finds it already there.

Revision ID: 0044_agent_message_client_id
Revises: 0043_surface_groups
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_agent_message_client_id"
down_revision = "0043_surface_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_message_client_message_id "
            "ON agent_messages "
            "(conversation_id, (message_metadata ->> 'client_message_id')) "
            "WHERE message_metadata ? 'client_message_id'"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_agent_message_client_message_id"))
