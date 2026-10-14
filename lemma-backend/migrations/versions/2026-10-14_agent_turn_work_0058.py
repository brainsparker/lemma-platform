"""Agent turns move onto the work runtime.

A turn was started by an ``AgentRunStartedEvent`` in the outbox, relayed onto
the ``agent_events`` stream, consumed by the ``agent-events`` group and
enqueued as a streaq job with a Redis heartbeat, and a turn that any of those
lost was failed by a ten-minute sweep. Now the transaction that creates a run
writes its ``agent.turn`` item, and this is the cutover:

* every RUNNING or STOP_REQUESTED run -> ``agent.turn`` (``agent.turn.automation``
  for a conversation a schedule or a workflow opened), due now, keyed
  ``agent-run:<id>`` and limited to one per conversation. A run an old worker is
  still executing during the rolling deploy is fenced by the conversation key
  once that worker's job ends, and the item finds the run where the old job
  left it -- finished (a no-op), stopped, or still RUNNING, which resumes from
  its last committed step;
* every ACTIVE AGENT workflow wait -> ``workflow.wait_expire``, due now: it
  resumes a step whose agent already finished (a completion still in the old
  consumer group's backlog), and otherwise re-arms itself at the wait's real
  ceiling -- the job the five-minute reconciliation sweep used to do.

And a sub-agent spawn becomes unique on the call that made it:
``agent_conversations.spawn_tool_call_id`` (``<parent run>:<tool call>``) with a
unique partial index per parent, so a resumed turn re-running an interrupted
``spawn_subagent`` gets the child it already has.

Revision ID: 0058_agent_turn_work
Revises: 0057_record_change_log
"""

import sqlalchemy as sa
from alembic import op

revision = "0058_agent_turn_work"
down_revision = "0057_record_change_log"
branch_labels = None
depends_on = None

#: The classes' per-pod caps; the runtime refreshes them from the limits port
#: within a minute, so these only have to be sane starting values.
_CAPS = {"events": 16, "automation": 32, "interactive": 48}

_ON_CONFLICT = (
    "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
    "DO NOTHING"
)

_RUN_NS = "'pod:' || c.pod_id::text"
_AUTOMATED = (
    "(upper(coalesce(c.conversation_metadata->>'source', '')) = 'SCHEDULE' "
    "OR c.conversation_metadata ? 'workflow_run_id')"
)
_OPEN_RUNS = (
    "agent_runs r JOIN agent_conversations c ON c.id = r.conversation_id "
    "WHERE r.status IN ('RUNNING', 'STOP_REQUESTED')"
)
_WAIT_NS = "'pod:' || w.pod_id::text"
_AGENT_WAITS = (
    "workflow_run_waits w WHERE w.status = 'ACTIVE' AND w.wait_type = 'AGENT'"
)


def _queues(cls: str, ns: str, source: str) -> str:
    return (
        "INSERT INTO work_queues (class, ns, max_running) "
        f"SELECT DISTINCT '{cls}', {ns}, {_CAPS[cls]} FROM {source} "
        "ON CONFLICT (class, ns) DO NOTHING"
    )


def _turns(cls: str, kind: str, source: str) -> str:
    return (
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, "
        "concurrency_key, concurrency_limit, run_at) "
        f"SELECT gen_random_uuid(), {_RUN_NS}, '{cls}', '{kind}', "
        "jsonb_build_object('run_id', r.id::text, 'conversation_id', "
        "c.id::text, 'user_id', c.user_id::text, 'agent_name', NULL), "
        "'agent-run:' || r.id::text, 'conversation:' || c.id::text, 1, now() "
        f"FROM {source} {_ON_CONFLICT}"
    )


def seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    interactive_runs = f"{_OPEN_RUNS} AND NOT {_AUTOMATED}"
    automated_runs = f"{_OPEN_RUNS} AND {_AUTOMATED}"
    return [
        _queues("interactive", _RUN_NS, interactive_runs),
        _queues("automation", _RUN_NS, automated_runs),
        _queues("events", _WAIT_NS, _AGENT_WAITS),
        _turns("interactive", "agent.turn", interactive_runs),
        _turns("automation", "agent.turn.automation", automated_runs),
        (
            "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
            f"SELECT gen_random_uuid(), {_WAIT_NS}, 'events', 'workflow.wait_expire', "
            "jsonb_build_object('wait_id', w.id::text), "
            f"'workflow-expire:' || w.id::text, now() FROM {_AGENT_WAITS} {_ON_CONFLICT}"
        ),
    ]


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column("spawn_tool_call_id", sa.String(300), nullable=True),
    )
    for statement in seed_statements():
        op.execute(sa.text(statement))
    # Concurrently, outside the transaction: conversations are written on
    # every turn, and a plain build blocks those writers for the scan. The
    # column is new and empty, so the scan finds nothing to index.
    with op.get_context().autocommit_block():
        op.create_index(
            "uq_agent_conv_spawn_call",
            "agent_conversations",
            ["parent_id", "spawn_tool_call_id"],
            unique=True,
            postgresql_where=sa.text("spawn_tool_call_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "uq_agent_conv_spawn_call",
            table_name="agent_conversations",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_column("agent_conversations", "spawn_tool_call_id")
    op.execute(
        sa.text(
            "DELETE FROM work_items WHERE kind IN ('agent.turn', "
            "'agent.turn.automation', 'agent.title', 'agent.followup', "
            "'agent.subagent_result', 'agent.replies_arrived', 'pod_ask.deliver', "
            "'workflow.resume_for_agent', 'schedule.agent_finished')"
        )
    )
