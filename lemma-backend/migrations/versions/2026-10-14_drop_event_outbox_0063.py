"""Drop the domain event outbox and inbox.

PLACEHOLDER, owned by PR 3 workstream D (removal, D2). Allocated empty in W0 so the four
PR 3 workstreams never pick the same number and collide on merge; the owner
replaces the body (and this docstring) and keeps the filename, revision id and
down_revision. Must not ship empty: a migration that has shipped cannot be
edited.

Planned: drop ``domain_event_outbox``, ``domain_event_inbox`` and the delivery
tables; the downgrade recreates them empty. If D2 ships as a separate PR after
PR 3, delete this placeholder before PR 3 merges and let D2 take the next
free number then. (Was 0061; D1 took 0061 for the pod-bundle cutover, which
ships with PR 3 either way.)

Revision ID: 0063_drop_event_outbox
Revises: 0062_pod_bundle_work
"""

revision = "0063_drop_event_outbox"
down_revision = "0062_pod_bundle_work"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
