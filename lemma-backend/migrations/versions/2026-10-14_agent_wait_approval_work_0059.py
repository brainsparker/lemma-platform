"""Agent approvals and waits move onto the work runtime.

PLACEHOLDER, owned by PR 3 workstream B (approvals and waits). Allocated empty in W0 so the four
PR 3 workstreams never pick the same number and collide on merge; the owner
replaces the body (and this docstring) and keeps the filename, revision id and
down_revision. Must not ship empty: a migration that has shipped cannot be
edited.

Planned: seed ``agent.wait.fire`` for every ACTIVE wait and
``agent.approval.execute`` for every approved request with no tool return;
drop ``fire_lease_until`` / ``wake_attempts`` from the waits table.

Revision ID: 0059_agent_wait_approval_work
Revises: 0058_agent_turn_work
"""

revision = "0059_agent_wait_approval_work"
down_revision = "0058_agent_turn_work"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
