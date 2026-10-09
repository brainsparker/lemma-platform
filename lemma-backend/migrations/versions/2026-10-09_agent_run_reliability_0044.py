"""Resume a reclaimed Agent Host run where it stopped, and store a re-sent message once.

**Agent Host run resume.** A worker that is stopped mid-run -- a deploy, a
SIGTERM -- leaves the run RUNNING for the next worker to reclaim. That worker
used to read the run's event stream from its first entry with a fresh
normalizer, and appended every narration, tool call and permission card already
in the conversation a second time. It also minted a new delegated token for a
dispatch that already existed, and threw it away. Two nullable columns on the
run's lease are what it needs instead:

* ``consumed_stream_id`` -- the last stream entry the previous worker had
  fully persisted, at a point where nothing it had read was still held
  unpersisted. The next worker resumes reading after it, and the stream is only
  ever trimmed below it, so an entry no worker has persisted is never dropped.
* ``credential_expires_at`` -- when the Lemma credential the host holds for the
  run expires, kept current as refreshes land, so a resumed worker can keep
  renewing it without minting a replacement first.

Null on every existing lease, which reads as "from the start" and "unknown",
exactly as before.

**One stored message per send.** A client that does not hear back from a send
-- a dropped connection, a double click, a retry after a timeout -- sends it
again. Each copy used to become its own message, and either a second run or a
second ask steered into the first. A send now carries a ``client_message_id``,
stamped into the message's metadata by the server, and a second send with the
same id in the same conversation returns the first one's run. The check itself
happens under the conversation lock, where the two copies are serialised; this
index is the backstop that holds even if a writer ever skips the lock. Partial
on the key being present, so messages written without one -- everything before
this, and every message a chat platform or the agent writes -- cost nothing in
it.

Operator note: building the index holds a SHARE lock on ``agent_messages`` for
the length of one scan, which blocks message writes but not reads. On an
installation where that table is large, build it first, out of band, with

    CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS
        uq_agent_message_client_message_id
        ON agent_messages (conversation_id, (message_metadata ->> 'client_message_id'))
        WHERE message_metadata ? 'client_message_id';

and this migration then finds it already there.

Revision ID: 0044_agent_run_reliability
Revises: 0043_surface_groups
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_agent_run_reliability"
down_revision = "0043_surface_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_host_run_leases",
        sa.Column("consumed_stream_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_host_run_leases",
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
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
    op.drop_column("agent_host_run_leases", "credential_expires_at")
    op.drop_column("agent_host_run_leases", "consumed_stream_id")
