"""Surface inbound messages, deliveries and receiver state.

PLACEHOLDER, owned by PR 3 workstream C (surfaces). Allocated empty in W0 so the four
PR 3 workstreams never pick the same number and collide on merge; the owner
replaces the body (and this docstring) and keeps the filename, revision id and
down_revision. Must not ship empty: a migration that has shipped cannot be
edited.

Planned: create ``surface_inbound_messages``, ``surface_deliveries`` and
``surface_receiver_state``.

Revision ID: 0060_surface_inbound_deliveries
Revises: 0059_agent_wait_approval_work
"""

revision = "0060_surface_inbound_deliveries"
down_revision = "0059_agent_wait_approval_work"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
