"""Record what proved a cached platform sender, and drop handle-only Telegram ones.

`agent_surface_external_users` caches who a platform sender is. It recorded the
answer and never what produced it, so a Telegram account bound because its
@username equalled a profile's free-text `telegram_username` looked exactly like
one bound by a verified number. A handle is a claim anybody can type onto their
own profile, and a handle given up on Telegram is taken by a stranger, so such a
binding ran the stranger as the profile's owner -- for good, since nothing
re-asked the cache.

``proven_by`` names the proof beside the resolution (PHONE, EMAIL, SETUP_LINK),
and a handle is never one. NULL on every existing row, which reads as
"unrecorded".

The update clears the existing Telegram resolutions a handle could have made: no
recorded proof, no number on the row (a self-shared contact leaves one), and a
resolved user who declares a handle. Those senders are asked to prove the
account on their next message instead of acting as it. Identity resolution
applies the same rule to any such row written after this runs.

Revision ID: 0045_surface_sender_proof
Revises: 0044_agent_host_run_resume
"""

import sqlalchemy as sa
from alembic import op

revision = "0045_surface_sender_proof"
down_revision = "0044_agent_host_run_resume"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    # The cleared resolutions are not restored: they were never proof.
    op.drop_column("agent_surface_external_users", "proven_by")
