"""Retire the old event transport: ``domain_event_outbox`` and ``domain_event_inbox``.

Nothing in this release writes or reads either table. What they still owe is
taken over by the cutover sweep this queues (``cutover.legacy_sweep``,
``app/core/infrastructure/cutover``), from the first worker boot:

* each delivery the old transport still owed -- an outbox row that is not dead
  and has no settled inbox row for a consumer whose work moved to the work
  runtime -- becomes one ``cutover.legacy_event`` item, and that consumer's
  inbox row is settled in the same statement, so an old worker still running
  during the rolling deploy does not do it as well;
* once neither table has been written for ten minutes (the previous release
  has stopped), the streaq jobs only Redis held are converted, the owed
  replies and open notifications are given their work, and both tables are
  dropped in one locked transaction after a last take-over.

Not here, because this migration runs *before* the new release rolls out:
the previous release keeps writing both tables until its last process stops,
and a dropped table would fail every transaction it commits. A fresh install
-- both tables empty -- has nothing to take over and no previous release, so
it drops them now.

This replaces the earlier design, where a separate release carried converters
and this migration refused to run until an operator's check passed: one
upgrade is safe on its own, from any earlier release, with nobody in the loop.
``python -m app.cutover_check`` reports the sweep's progress.

The converter items the earlier migrations of this stack queued
(``changes.drain`` in 0058) have no handler in this release and are deleted;
the sweep covers what they would have read.

The downgrade recreates whichever table is missing, empty, and removes the
cutover's items.

Revision ID: 0063_drop_event_outbox
Revises: 0062_pod_bundle_work
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063_drop_event_outbox"
down_revision = "0062_pod_bundle_work"
branch_labels = None
depends_on = None

#: Converters of the earlier, two-release design; none is registered now.
_RETIRED_CONVERTERS = (
    "changes.drain",
    "surfaces.drain_events",
    "workflow.drain_schedule_fires",
)
_CUTOVER_KINDS = ("cutover.legacy_sweep", "cutover.legacy_event")
_ADMIN_DEFAULT_CAP = 1


def _exists(table: str) -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text(f"SELECT to_regclass('{table}') IS NOT NULL"))
        .scalar()
    )


def _empty(table: str) -> bool:
    return not _exists(table) or not (
        op.get_bind()
        .execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})"))
        .scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM work_items WHERE kind = ANY(:kinds)"),
        {"kinds": list(_RETIRED_CONVERTERS)},
    )
    bind.execute(
        sa.text(
            "INSERT INTO work_queues (class, ns, max_running) "
            f"VALUES ('admin', 'system', {_ADMIN_DEFAULT_CAP}) "
            "ON CONFLICT (class, ns) DO NOTHING"
        )
    )
    # Always: a fresh install's sweep still retires what Redis holds and
    # gives open notifications and owed replies their work.
    bind.execute(
        sa.text(
            "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
            "VALUES (gen_random_uuid(), 'system', 'admin', 'cutover.legacy_sweep', "
            "'{}'::jsonb, 'cutover.legacy_sweep', now()) "
            "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
            "DO NOTHING"
        )
    )
    if _empty("domain_event_outbox") and _empty("domain_event_inbox"):
        op.execute(sa.text("DROP TABLE IF EXISTS domain_event_inbox"))
        op.execute(sa.text("DROP TABLE IF EXISTS domain_event_outbox"))


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM work_items WHERE kind = ANY(:kinds)"),
        {"kinds": list(_CUTOVER_KINDS)},
    )
    _recreate_empty()


def _recreate_empty() -> None:
    """The tables as 0003 created them, empty -- each only if it is missing."""
    if not _exists("domain_event_outbox"):
        _create_outbox()
    if not _exists("domain_event_inbox"):
        _create_inbox()


def _create_outbox() -> None:
    op.create_table(
        "domain_event_outbox",
        sa.Column("stream", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("producer", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=160), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_domain_event_outbox_ready",
        "domain_event_outbox",
        ["available_at", "occurred_at", "id"],
        postgresql_where=sa.text("published_at IS NULL AND dead_lettered_at IS NULL"),
    )
    op.create_index(
        "ix_domain_event_outbox_expired_lease",
        "domain_event_outbox",
        ["lease_until", "occurred_at", "id"],
        postgresql_where=sa.text(
            "lease_until IS NOT NULL AND published_at IS NULL "
            "AND dead_lettered_at IS NULL"
        ),
    )
    op.create_index(
        "ix_domain_event_outbox_published_retention",
        "domain_event_outbox",
        ["published_at"],
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_event_outbox_dlq_listing",
        "domain_event_outbox",
        [sa.text("dead_lettered_at DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("dead_lettered_at IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_event_outbox_occurred",
        "domain_event_outbox",
        [sa.text("occurred_at DESC"), sa.text("id DESC")],
    )


def _create_inbox() -> None:
    op.create_table(
        "domain_event_inbox",
        sa.Column("consumer", sa.String(length=200), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="PROCESSING", nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("delivery_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consumer", "event_id", name="uq_domain_event_inbox_consumer_event"
        ),
    )
    op.create_index(
        "ix_domain_event_inbox_status_received",
        "domain_event_inbox",
        ["status", "last_received_at"],
    )
    op.create_index(
        "ix_domain_event_inbox_completed_retention",
        "domain_event_inbox",
        ["completed_at"],
        postgresql_where=sa.text("completed_at IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_event_inbox_dlq_retention",
        "domain_event_inbox",
        ["dead_lettered_at"],
        postgresql_where=sa.text("dead_lettered_at IS NOT NULL"),
    )
