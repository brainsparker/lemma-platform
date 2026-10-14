"""Agent turns move onto the work runtime.

PLACEHOLDER, owned by PR 3 workstream A (agent turns). Allocated empty in W0 so the four
PR 3 workstreams never pick the same number and collide on merge; the owner
replaces the body (and this docstring) and keeps the filename, revision id and
down_revision. Must not ship empty: a migration that has shipped cannot be
edited.

Planned: seed ``agent.turn`` for every RUNNING / STOP_REQUESTED run; add
``spawn_tool_call_id`` to child conversations with a unique partial index on
``(parent_conversation_id, spawn_tool_call_id)``.

Revision ID: 0058_agent_turn_work
Revises: 0057_record_change_log
"""

revision = "0058_agent_turn_work"
down_revision = "0057_record_change_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
