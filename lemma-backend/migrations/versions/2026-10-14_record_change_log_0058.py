"""Record changes move to each pod's change log: MCP deliveries, and the cutover.

One table:

* ``mcp_event_deliveries`` -- one row per record change owed to one MCP event
  subscription, unique on ``(subscription, change)``, written by the change
  log's delivery in the transaction that decides the change matches.

And the cutover. DATASTORE triggers and MCP event subscriptions used to read
``datastore.events`` through two consumer groups; now each is a durable
subscriber of its pod's change log. Every active DATASTORE schedule and every
live MCP subscription gets a ``changes.sync_subscribers`` item, which creates
its subscriber row at the log's current horizon. One ``changes.drain`` item
then converts whatever the old path still held -- datastore outbox rows and
both groups' unfinished entries -- into change-log rows, after those syncs,
so nothing the old path owed is lost.

Revision ID: 0058_record_change_log
Revises: 0057_flow_work_items
"""

import sqlalchemy as sa
from alembic import op

revision = "0058_record_change_log"
down_revision = "0057_flow_work_items"
branch_labels = None
depends_on = None

#: The classes' per-pod caps; the runtime refreshes them from the limits port
#: within a minute, so these only have to be sane starting values.
_EVENTS_DEFAULT_CAP = 16
_ADMIN_DEFAULT_CAP = 1

_DATASTORE_TRIGGERS = (
    "SELECT s.pod_id, 'schedule:' || s.id::text AS subscriber FROM schedules s "
    "WHERE s.schedule_type = 'DATASTORE' AND s.is_active AND s.pod_id IS NOT NULL"
)
_MCP_SUBSCRIPTIONS = (
    "SELECT m.pod_id, 'mcp:' || m.public_id AS subscriber "
    "FROM mcp_event_subscriptions m "
    "WHERE m.stopped_at IS NULL AND m.refresh_before > now()"
)


def seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    subscribers = f"({_DATASTORE_TRIGGERS} UNION ALL {_MCP_SUBSCRIPTIONS}) AS w"
    return [
        (
            "INSERT INTO work_queues (class, ns, max_running) "
            f"SELECT DISTINCT 'events', 'pod:' || w.pod_id::text, {_EVENTS_DEFAULT_CAP} "
            f"FROM {subscribers} ON CONFLICT (class, ns) DO NOTHING"
        ),
        (
            "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, "
            "concurrency_key, concurrency_limit, run_at) "
            "SELECT gen_random_uuid(), 'pod:' || w.pod_id::text, 'events', "
            "'changes.sync_subscribers', "
            "jsonb_build_object('pod_id', w.pod_id::text, 'subscriber', w.subscriber), "
            "'changes.sync:' || w.pod_id::text || ':' || w.subscriber, "
            "'changes:' || w.pod_id::text || ':' || w.subscriber, 1, now() "
            f"FROM {subscribers} "
            "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
            "DO NOTHING"
        ),
        (
            "INSERT INTO work_queues (class, ns, max_running) "
            f"VALUES ('admin', 'system', {_ADMIN_DEFAULT_CAP}) "
            "ON CONFLICT (class, ns) DO NOTHING"
        ),
        (
            "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
            "VALUES (gen_random_uuid(), 'system', 'admin', 'changes.drain', "
            "'{}'::jsonb, 'changes.drain', now()) "
            "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
            "DO NOTHING"
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "mcp_event_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Uuid(),
            sa.ForeignKey("mcp_event_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("change_key", sa.String(80), nullable=False),
        sa.Column("pod_id", sa.Uuid(), nullable=False),
        sa.Column("table_name", sa.String(255), nullable=False),
        sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "subscription_id", "change_key", name="uq_mcp_event_deliveries_change"
        ),
    )
    op.create_index(
        "ix_mcp_event_deliveries_created", "mcp_event_deliveries", ["created_at"]
    )
    for statement in seed_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "DELETE FROM work_items WHERE kind IN ('changes.sync_subscribers', "
        "'changes.deliver', 'changes.drain', 'changes.retention', 'mcp.deliver')"
    )
    op.drop_index("ix_mcp_event_deliveries_created", table_name="mcp_event_deliveries")
    op.drop_table("mcp_event_deliveries")
