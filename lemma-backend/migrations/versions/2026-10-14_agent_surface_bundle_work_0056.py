"""Agents, approvals, waits, surfaces and pod bundles move onto the work runtime.

Additive only: nothing here drops a table or a column the previous release
reads, so an older image still finds everything it uses
(``0057_drop_old_transport`` drops the columns; ``finish`` drops the old
transport's tables, once an operator -- or, on a single process install, the
sweep -- has seen it owe nothing).

**Agent turns.** A turn was started by an ``AgentRunStartedEvent`` in the
outbox, relayed onto a stream, consumed by a group and enqueued as a streaq job
with a Redis heartbeat; a turn any of those lost was failed by a ten-minute
sweep. Now the transaction that creates a run writes its ``agent.turn`` item:

* every RUNNING or STOP_REQUESTED run -> ``agent.turn`` (``agent.turn.automation``
  for a conversation a schedule or a workflow opened), due now, keyed
  ``agent-run:<id>`` and limited to one per conversation. A run an old worker is
  still executing during the rolling deploy is fenced by the conversation key
  once that worker's job ends, and the item finds the run where the old job
  left it -- finished (a no-op), stopped, or still RUNNING, which resumes from
  its last committed step;
* every ACTIVE AGENT workflow wait -> ``workflow.wait_expire``, due now.

A sub-agent spawn becomes unique on the call that made it
(``agent_conversations.spawn_tool_call_id``, unique per parent), so a resumed
turn re-running an interrupted ``spawn_subagent`` gets the child it already has.

**Approvals and waits.**

* an ACTIVE TIME or SUBAGENT wait -> ``agent.wait.fire`` at its ``scheduled_at``;
* an ACTIVE PROCESS wait -> ``agent.wait.poll`` at its ``scheduled_at``;
* a recorded decision whose call has no tool return -> ``agent.approval.execute``.
  Unclaimed, due now. Claimed, due once the old job's own ceiling (2,100 s
  after the claim) has passed, so a job an old worker is still running during
  the rolling deploy is not pre-empted.

The waits' ``fire_lease_until`` and ``wake_attempts`` are no longer read; they
stay until ``0057`` so the previous release keeps working.

**Surfaces.** ``surface_inbound_messages`` (one row per inbound delivery,
unique on ``(receiver_scope, source_event_id)`` -- the dedup, with no TTL),
``surface_message_claims``, ``surface_deliveries`` (``PENDING -> SENDING ->
SENT | UNKNOWN | FAILED``) and ``surface_receiver_state`` replace the Redis
delivery claims, ``event_dedup`` keys, owed-reply metadata and receiver
cursors.

**Pod bundles.** Each in-flight bundle job gets the item that runs it, due 31
minutes after its last heartbeat, so it never runs beside an old streaq job
(30-minute timeout). A URL import not yet copied is left to the stale-job
sweep: its source is named only in the request's signed token.

**The old transport.** ``cutover.legacy_sweep`` is queued once. From the first
worker boot it turns every delivery the old transport still owes into a
``cutover.legacy_event`` item and settles the old inbox row in the same
statement, and once the previous release has stopped it converts what only
Redis held. It does not drop anything. The converters of an earlier design
(``changes.drain`` and two more) have no handler in this release and are
deleted; the sweep covers what they would have read.

Every step is idempotent: the concurrent index builds commit on their own, so
a failure after them leaves the revision half applied, and a rerun must finish
it rather than trip over what is already there.

Revision ID: 0056_agent_surface_bundle_work
Revises: 0055_change_log_and_flows
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0056_agent_surface_bundle_work"
down_revision = "0055_change_log_and_flows"
branch_labels = None
depends_on = None

#: The classes' per-pod caps; the runtime refreshes them from the limits port
#: within a minute, so these only have to be sane starting values.
_CAPS = {"events": 16, "automation": 32, "interactive": 48, "admin": 1}

_ON_CONFLICT = (
    "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
    "DO NOTHING"
)

#: How recently something must have moved to count as in flight when the
#: release goes out. Older work is not resumed on its own: it is failed with a
#: reason a person can act on (send a message, resume the import).
_LIVE_WINDOW = "interval '30 minutes'"
_HORIZON = f"now() - {_LIVE_WINDOW}"
_INTERRUPTED = "Interrupted before the upgrade. Send a message to continue."


def park_statements() -> list[str]:
    """What is too old to resume, failed saying why -- before the seeds, which
    then find nothing of it in a state they would seed."""
    runs = (
        "WITH stale AS ("
        "UPDATE agent_runs r SET status = 'FAILED', finished_at = now(), "
        f"error = '{_INTERRUPTED}' FROM agent_conversations c "
        "WHERE c.id = r.conversation_id AND r.status = 'RUNNING' "
        "AND GREATEST(r.updated_at, r.started_at, c.last_activity_at) "
        f"< {_HORIZON} RETURNING r.conversation_id) "
        "UPDATE agent_conversations c SET status = 'FAILED' FROM stale "
        "WHERE c.id = stale.conversation_id AND c.status = 'RUNNING'"
    )
    waits = (
        "WITH stale AS ("
        "UPDATE agent_conversation_waits w SET status = 'FAILED', "
        "completed_at = now() WHERE w.status = 'ACTIVE' "
        "AND w.wait_type IN ('TIME', 'SUBAGENT', 'PROCESS') "
        f"AND w.scheduled_at < {_HORIZON} RETURNING w.conversation_id) "
        "UPDATE agent_conversations c SET status = 'FAILED' FROM stale "
        "WHERE c.id = stale.conversation_id AND c.status = 'WAITING'"
    )
    bundles = (
        "UPDATE pod_bundle_jobs j SET status = 'FAILED', completed_at = now(), "
        "error_code = 'NOT_FINISHED_BEFORE_UPGRADE', "
        "error = 'Not finished before the upgrade. Start it again to retry.' "
        "WHERE j.status IN ('QUEUED', 'EXPORTING', 'APPLYING', 'FETCHING', "
        f"'PLANNING') AND COALESCE(j.heartbeat_at, j.updated_at) < {_HORIZON}"
    )
    return [runs, waits, bundles]


# -- agent turns ---------------------------------------------------------------

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


def _repair_invalid(name: str) -> None:
    """Drop a half-built index so `IF NOT EXISTS` stops mistaking it for done.

    An interrupted `CREATE INDEX CONCURRENTLY` leaves the index behind with
    `indisvalid = false`. The name exists, so on the rerun `IF NOT EXISTS`
    skips the build, the revision is stamped, and the planner never uses the
    index -- or, for a unique one, never enforces it. Checking first is what
    makes a rerun recover (the same guard as 0038 and 0053).
    """
    half_built = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": name},
        )
        .scalar()
    )
    if half_built:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


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


def agent_turn_seed_statements() -> list[str]:
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


_AGENT_TURN_KINDS = (
    "'agent.turn', 'agent.turn.automation', 'agent.title', 'agent.followup', "
    "'agent.subagent_result', 'agent.replies_arrived', 'pod_ask.deliver', "
    "'workflow.resume_for_agent', 'schedule.agent_finished'"
)


# -- approvals and waits -------------------------------------------------------

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


def agent_wait_seed_statements() -> list[str]:
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
        f"FROM {_OPEN_DECISIONS} AND d.created_at >= {_HORIZON} {_ON_CONFLICT}"
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


_AGENT_WAIT_KINDS = "'agent.wait.fire', 'agent.wait.poll', 'agent.approval.execute'"


# -- surfaces -------------------------------------------------------------------


def _now(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()"),
    )


def _create_surface_tables() -> None:
    op.create_table(
        "surface_inbound_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("receiver_scope", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("surface_id", UUID(as_uuid=True), nullable=True),
        sa.Column("pod_id", UUID(as_uuid=True), nullable=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "headers", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("receiver_surface_ids", JSONB(), nullable=True),
        sa.Column("thread_key", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'RECEIVED'")
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", UUID(as_uuid=True), nullable=True),
        _now("received_at"),
        sa.Column("routed_at", sa.DateTime(timezone=True), nullable=True),
        _now("updated_at"),
        sa.CheckConstraint(
            "status IN ('RECEIVED', 'INGESTING', 'ROUTED', 'DROPPED', 'FAILED')",
            name="ck_surface_inbound_messages_status",
        ),
        sa.UniqueConstraint(
            "receiver_scope",
            "source_event_id",
            name="uq_surface_inbound_messages_receiver_event",
        ),
        if_not_exists=True,
    )
    # Retention reads finished rows by age.
    op.create_index(
        "ix_surface_inbound_messages_status_received",
        "surface_inbound_messages",
        ["status", "received_at"],
        if_not_exists=True,
    )

    op.create_table(
        "surface_message_claims",
        sa.Column("claim_key", sa.Text(), primary_key=True),
        sa.Column("owner", sa.Text(), nullable=True),
        _now("created_at"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_surface_message_claims_created_at",
        "surface_message_claims",
        ["created_at"],
        if_not_exists=True,
    )

    op.create_table(
        "surface_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("pod_id", UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("notification_id", UUID(as_uuid=True), nullable=True),
        sa.Column("inbound_message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("surface_id", UUID(as_uuid=True), nullable=True),
        sa.Column("destination_key", sa.Text(), nullable=False),
        sa.Column("target", JSONB(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        _now("not_before"),
        sa.Column("sending_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'SENT', 'UNKNOWN', 'FAILED')",
            name="ck_surface_deliveries_status",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_surface_deliveries_idempotency_key"
        ),
        if_not_exists=True,
    )
    # The destination pump: the oldest unfinished delivery for one destination.
    op.create_index(
        "ix_surface_deliveries_destination_open",
        "surface_deliveries",
        ["destination_key", "created_at"],
        postgresql_where=sa.text("status IN ('PENDING', 'SENDING')"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_surface_deliveries_status_created",
        "surface_deliveries",
        ["status", "created_at"],
        if_not_exists=True,
    )

    op.create_table(
        "surface_receiver_state",
        sa.Column("receiver_key", sa.Text(), primary_key=True),
        sa.Column("cursor", sa.Text(), nullable=True),
        _now("updated_at"),
        if_not_exists=True,
    )


def _drop_surface_tables() -> None:
    op.drop_table("surface_receiver_state")
    op.drop_index(
        "ix_surface_deliveries_status_created", table_name="surface_deliveries"
    )
    op.drop_index(
        "ix_surface_deliveries_destination_open", table_name="surface_deliveries"
    )
    op.drop_table("surface_deliveries")
    op.drop_index(
        "ix_surface_message_claims_created_at", table_name="surface_message_claims"
    )
    op.drop_table("surface_message_claims")
    op.drop_index(
        "ix_surface_inbound_messages_status_received",
        table_name="surface_inbound_messages",
    )
    op.drop_table("surface_inbound_messages")


# -- pod bundles ----------------------------------------------------------------

_BUNDLE_NS = "'pod:' || j.pod_id::text"
_DUE = "GREATEST(now(), COALESCE(j.heartbeat_at, j.updated_at) + interval '31 minutes')"
_IDS = "'pod_id', j.pod_id::text, 'user_id', j.user_id::text"
_IMPORT_PLANNING = (
    "j.job_kind = 'IMPORT' AND j.status IN ('QUEUED', 'FETCHING', 'PLANNING')"
)

#: (kind, job id field, dedup prefix, extra payload, which jobs)
_SEEDS = (
    (
        "pod_bundle.export",
        "export_id",
        "pod-export:",
        ", 'origin', NULL, 'origin_platform', NULL",
        "j.job_kind = 'EXPORT' AND j.status IN ('QUEUED', 'EXPORTING')",
    ),
    (
        "pod_bundle.apply",
        "import_id",
        "pod-import:",
        "",
        "j.job_kind = 'IMPORT' AND j.status = 'APPLYING'",
    ),
    (
        "pod_bundle.plan",
        "import_id",
        "pod-import-plan:",
        "",
        f"{_IMPORT_PLANNING} AND j.snapshot->>'staging_key' IS NOT NULL",
    ),
    (
        "pod_bundle.import_github",
        "import_id",
        "pod-import-plan:",
        ", 'owner', NULL, 'repo', NULL, 'account_id', j.snapshot->>'account_id'",
        (
            f"{_IMPORT_PLANNING} AND j.snapshot->>'staging_key' IS NULL "
            "AND j.snapshot->'source'->>'kind' = 'GITHUB'"
        ),
    ),
    (
        "pod_bundle.publish",
        "publish_id",
        "pod-publish:",
        ", 'origin', NULL, 'origin_platform', NULL",
        "j.job_kind = 'PUBLISH' AND j.status IN ('QUEUED', 'EXPORTING', 'PUBLISHING')",
    ),
)


def bundle_seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    statements = []
    for _kind, _field, _prefix, _extra, where in _SEEDS:
        statements.append(
            "INSERT INTO work_queues (class, ns, max_running) "
            f"SELECT DISTINCT 'admin', {_BUNDLE_NS}, 1 FROM pod_bundle_jobs j "
            f"WHERE {where} ON CONFLICT (class, ns) DO NOTHING"
        )
    for kind, field, prefix, extra, where in _SEEDS:
        statements.append(
            "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, "
            "concurrency_key, concurrency_limit, run_at) "
            f"SELECT gen_random_uuid(), {_BUNDLE_NS}, 'admin', '{kind}', "
            f"jsonb_build_object('{field}', j.id::text, {_IDS}{extra}), "
            f"'{prefix}' || j.id::text, 'pod_bundle:' || j.pod_id::text, 1, "
            f"{_DUE} FROM pod_bundle_jobs j WHERE {where} {_ON_CONFLICT}"
        )
    return statements


_BUNDLE_KINDS = tuple(kind for kind, *_ in _SEEDS)


# -- the old transport ----------------------------------------------------------

#: Converters of an earlier, two-release design; none is registered now.
_RETIRED_CONVERTERS = (
    "changes.drain",
    "surfaces.drain_events",
    "workflow.drain_schedule_fires",
)
_CUTOVER_KINDS = ("cutover.legacy_sweep", "cutover.legacy_event")


def _exists(table: str) -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text(f"SELECT to_regclass('{table}') IS NOT NULL"))
        .scalar()
    )


def _queue_cutover_sweep() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM work_items WHERE kind = ANY(:kinds)"),
        {"kinds": list(_RETIRED_CONVERTERS)},
    )
    bind.execute(
        sa.text(
            "INSERT INTO work_queues (class, ns, max_running) "
            f"VALUES ('admin', 'system', {_CAPS['admin']}) "
            "ON CONFLICT (class, ns) DO NOTHING"
        )
    )
    # Always: a fresh install's sweep still retires what Redis holds and
    # gives open notifications and owed replies their work.
    bind.execute(
        sa.text(
            "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
            "VALUES (gen_random_uuid(), 'system', 'admin', 'cutover.legacy_sweep', "
            f"jsonb_build_object('horizon', {_HORIZON}), 'cutover.legacy_sweep', "
            "now()) "
            "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
            "DO NOTHING"
        )
    )


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column("spawn_tool_call_id", sa.String(300), nullable=True),
        if_not_exists=True,
    )
    _create_surface_tables()
    for statement in (
        *park_statements(),
        *agent_turn_seed_statements(),
        *agent_wait_seed_statements(),
        *bundle_seed_statements(),
    ):
        op.execute(sa.text(statement))
    _queue_cutover_sweep()
    # Concurrently, outside the transaction: conversations are written on
    # every turn, and the previous release keeps writing the outbox until its
    # last process stops; a plain build would block those writers.
    with op.get_context().autocommit_block():
        _repair_invalid("uq_agent_conv_spawn_call")
        op.create_index(
            "uq_agent_conv_spawn_call",
            "agent_conversations",
            ["parent_id", "spawn_tool_call_id"],
            unique=True,
            postgresql_where=sa.text("spawn_tool_call_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        # The sweep reads owed rows per event type, oldest first; the old
        # transport never needed that order.
        if _exists("domain_event_outbox"):
            _repair_invalid("ix_domain_event_outbox_cutover")
            op.create_index(
                "ix_domain_event_outbox_cutover",
                "domain_event_outbox",
                ["event_type", "occurred_at"],
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_domain_event_outbox_cutover",
            table_name="domain_event_outbox",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "uq_agent_conv_spawn_call",
            table_name="agent_conversations",
            postgresql_concurrently=True,
            if_exists=True,
        )
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM work_items WHERE kind = ANY(:kinds)"),
        {"kinds": list(_CUTOVER_KINDS)},
    )
    # Items only, for bundles: the jobs' rows are the old transport's state too.
    bind.execute(
        sa.text("DELETE FROM work_items WHERE kind = ANY(:kinds) AND state = 'queued'"),
        {"kinds": list(_BUNDLE_KINDS)},
    )
    op.execute(sa.text(f"DELETE FROM work_items WHERE kind IN ({_AGENT_WAIT_KINDS})"))
    op.execute(sa.text(f"DELETE FROM work_items WHERE kind IN ({_AGENT_TURN_KINDS})"))
    _drop_surface_tables()
    op.drop_column("agent_conversations", "spawn_tool_call_id")
