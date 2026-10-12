"""Resume a reclaimed Agent Host run, store a re-sent message once, and prove Telegram senders.

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

**What proved a cached platform sender.** ``agent_surface_external_users``
caches who a platform sender is. It recorded the answer and never what produced
it, so a Telegram account bound because its @username equalled a profile's
free-text ``telegram_username`` looked exactly like one bound by a verified
number. A handle is a claim anybody can type onto their own profile, and a
handle given up on Telegram is taken by a stranger, so such a binding ran the
stranger as the profile's owner -- for good, since nothing re-asked the cache.
``proven_by`` names the proof beside the resolution (PHONE, EMAIL, SETUP_LINK),
and a handle is never one. NULL on every existing row, which reads as
"unrecorded". The update clears the existing Telegram resolutions a handle could
have made: no recorded proof, no number on the row (a self-shared contact leaves
one), and a resolved user who declares a handle. Those senders are asked to
prove the account on their next message instead of acting as it. Identity
resolution applies the same rule to any such row written after this runs.

Revision ID: 0051_agent_run_reliability
Revises: 0050_pod_grantee_index
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_agent_run_reliability"
down_revision = "0050_pod_grantee_index"
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
    op.add_column(
        "agent_surface_external_users",
        sa.Column("proven_by", sa.String(length=32), nullable=True),
    )
    op.execute(
        """
        UPDATE agent_surface_external_users AS sender
        SET resolved_user_id = NULL
        FROM users
        WHERE sender.platform = 'TELEGRAM'
          AND sender.resolved_user_id = users.id
          AND sender.proven_by IS NULL
          AND sender.phone IS NULL
          AND COALESCE(users.telegram_username, '') <> ''
        """
    )


def downgrade() -> None:
    # The cleared Telegram resolutions are not restored: they were never proof.
    op.drop_column("agent_surface_external_users", "proven_by")
    op.execute(sa.text("DROP INDEX IF EXISTS uq_agent_message_client_message_id"))
    op.drop_column("agent_host_run_leases", "credential_expires_at")
    op.drop_column("agent_host_run_leases", "consumed_stream_id")
