"""Agent approvals and waits move onto the work runtime.

An approved ``request_approval`` was carried out by a streaq job enqueued after
the decision committed -- lost if the process died in between, recovered only
by the conversation's next message -- and an executor killed mid-tool was
written off 35 minutes later, whenever something happened to look. A
``wait_for`` was fired by the schedule poller claiming due rows, published on
``schedule_events``, routed through a consumer group, and swept every five
minutes for lost fires. Each is now a work item written with the state that
needs it, and this is the cutover:

* an ACTIVE TIME or SUBAGENT wait -> ``agent.wait.fire`` at its
  ``scheduled_at`` (a past one is due now);
* an ACTIVE PROCESS wait -> ``agent.wait.poll`` at its ``scheduled_at``;
* a recorded decision whose call has no tool return -> ``agent.approval.execute``.
  Unclaimed, due now. Claimed, due once the old job's own ceiling (2,100 s
  after the claim) has passed, so a job an old worker is still running during
  the rolling deploy is not pre-empted; the item then writes "approved,
  outcome unknown" unless that job wrote the real result first.

Repeats are harmless: every handler takes the wait's row lock (or the
execution claim) and acts only on state that is still open.

The waits' lease and attempt columns go: the item's own lease and attempt
budget replace them.

Revision ID: 0059_agent_wait_approval_work
Revises: 0058_agent_turn_work
"""

import sqlalchemy as sa
from alembic import op

revision = "0059_agent_wait_approval_work"
down_revision = "0058_agent_turn_work"
branch_labels = None
depends_on = None

#: The classes' per-pod caps; the runtime refreshes them from the limits port
#: within a minute, so these only have to be sane starting values.
_CAPS = {"events": 16, "automation": 32, "interactive": 48}

_ON_CONFLICT = (
    "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
    "DO NOTHING"
)

_WAIT_NS = "'pod:' || w.pod_id::text"
_TIMERS = (
    "agent_conversation_waits w WHERE w.status = 'ACTIVE' "
    "AND w.wait_type IN ('TIME', 'SUBAGENT')"
)
_POLLS = (
    "agent_conversation_waits w WHERE w.status = 'ACTIVE' AND w.wait_type = 'PROCESS'"
)

_APPROVAL_NS = "'pod:' || c.pod_id::text"
_APPROVAL_KEY = "'agent-approval:' || d.conversation_id::text || ':' || d.approval_id"
#: Decisions whose paused call exists and has no return yet.
_OPEN_DECISIONS = (
    "agent_approval_decisions d "
    "JOIN agent_conversations c ON c.id = d.conversation_id "
    "WHERE c.pod_id IS NOT NULL "
    "AND EXISTS (SELECT 1 FROM agent_messages m "
    " WHERE m.conversation_id = d.conversation_id "
    " AND m.tool_call_id = d.approval_id AND m.kind = 'TOOL_CALL') "
    "AND NOT EXISTS (SELECT 1 FROM agent_messages m "
    " WHERE m.conversation_id = d.conversation_id "
    " AND m.tool_call_id = d.approval_id AND m.kind = 'TOOL_RETURN')"
)
#: The retired job's ceiling (``_ABANDONED_EXECUTION_SECONDS``).
_OLD_EXECUTION_CEILING = "interval '2100 seconds'"


def _queues(cls: str, ns: str, source: str) -> str:
    return (
        "INSERT INTO work_queues (class, ns, max_running) "
        f"SELECT DISTINCT '{cls}', {ns}, {_CAPS[cls]} FROM {source} "
        "ON CONFLICT (class, ns) DO NOTHING"
    )


def seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    wait_payload = "jsonb_build_object('wait_id', w.id::text)"
    wait_dedup = "'wait:' || w.id::text"
    approval_payload = (
        "jsonb_build_object("
        "'conversation_id', d.conversation_id::text, "
        "'approval_id', d.approval_id, "
        "'user_id', COALESCE(d.resolved_by_user_id, c.user_id)::text, "
        "'pod_id', c.pod_id::text)"
    )
    approval_run_at = (
        "CASE WHEN d.execution_claimed_at IS NULL THEN now() ELSE GREATEST("
        f"now(), d.execution_claimed_at + {_OLD_EXECUTION_CEILING}) END"
    )
    wait_items = (
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
        "SELECT gen_random_uuid(), {ns}, '{cls}', '{kind}', {payload}, {dedup}, "
        "COALESCE(w.scheduled_at, now()) FROM {source} {conflict}"
    )
    approval_items = (
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, "
        "concurrency_key, concurrency_limit, run_at) "
        f"SELECT gen_random_uuid(), {_APPROVAL_NS}, 'interactive', "
        f"'agent.approval.execute', {approval_payload}, {_APPROVAL_KEY}, "
        f"{_APPROVAL_KEY}, 1, {approval_run_at} "
        f"FROM {_OPEN_DECISIONS} {_ON_CONFLICT}"
    )
    return [
        _queues("events", _WAIT_NS, _TIMERS),
        _queues("automation", _WAIT_NS, _POLLS),
        _queues("interactive", _APPROVAL_NS, _OPEN_DECISIONS),
        wait_items.format(
            ns=_WAIT_NS,
            cls="events",
            kind="agent.wait.fire",
            payload=wait_payload,
            dedup=wait_dedup,
            source=_TIMERS,
            conflict=_ON_CONFLICT,
        ),
        wait_items.format(
            ns=_WAIT_NS,
            cls="automation",
            kind="agent.wait.poll",
            payload=wait_payload,
            dedup=wait_dedup,
            source=_POLLS,
            conflict=_ON_CONFLICT,
        ),
        approval_items,
    ]


_SEEDED_KINDS = "'agent.wait.fire', 'agent.wait.poll', 'agent.approval.execute'"


def upgrade() -> None:
    for statement in seed_statements():
        op.execute(sa.text(statement))
    op.drop_column("agent_conversation_waits", "fire_lease_until")
    op.drop_column("agent_conversation_waits", "wake_attempts")


def downgrade() -> None:
    op.add_column(
        "agent_conversation_waits",
        sa.Column("wake_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agent_conversation_waits",
        sa.Column("fire_lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(sa.text(f"DELETE FROM work_items WHERE kind IN ({_SEEDED_KINDS})"))
