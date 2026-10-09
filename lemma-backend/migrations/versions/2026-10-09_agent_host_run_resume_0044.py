"""Let a reclaimed Agent Host run pick up where its last worker left off.

A worker that is stopped mid-run -- a deploy, a SIGTERM -- leaves the run
RUNNING for the next worker to reclaim. That worker used to read the run's
event stream from its first entry with a fresh normalizer, and appended every
narration, tool call and permission card already in the conversation a second
time. It also minted a new delegated token for a dispatch that already existed,
and threw it away.

Two nullable columns on the run's lease are what it needs instead:

* ``consumed_stream_id`` -- the last stream entry the previous worker had
  fully persisted, at a point where nothing it had read was still held
  unpersisted. The next worker resumes reading after it, and the stream is only
  ever trimmed below it, so an entry no worker has persisted is never dropped.
* ``credential_expires_at`` -- when the Lemma credential the host holds for the
  run expires, kept current as refreshes land, so a resumed worker can keep
  renewing it without minting a replacement first.

Null on every existing lease, which reads as "from the start" and "unknown",
exactly as before.

Revision ID: 0044_agent_host_run_resume
Revises: 0043_surface_groups
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_agent_host_run_resume"
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


def downgrade() -> None:
    op.drop_column("agent_host_run_leases", "credential_expires_at")
    op.drop_column("agent_host_run_leases", "consumed_stream_id")
