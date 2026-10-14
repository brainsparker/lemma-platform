"""Drop what is left of the old event transport: ``domain_event_outbox`` and ``domain_event_inbox``.

Nothing writes or reads either any more. The unit of work hands committed
events to the after-commit sink; the three cutover converters
(``changes.drain``, ``surfaces.drain_events``, ``workflow.drain_schedule_fires``)
that drained what the outbox still owed ship in the release *before* this one,
and are deleted with it (design §13 rule 4).

A fresh install never ran the old transport (both tables empty): the
converter items 0058-0062 queued are deleted, and the drop goes ahead.
Otherwise this refuses to run while the database half of the cutover gate fails --
anything else would drop work that exists nowhere else:

* a converter item still queued, running or dead;
* an unpublished outbox row of a type a converter owed
  (``surface.webhook.received``, ``surface.onboarding.ready``,
  ``notification.settled``, ``schedule.fired``), or a record event
  (``datastore.record.*``) -- the datastore outbox when it shares this
  database.

The Redis half (legacy groups drained, no streaq job left) and a separate
datastore database are checked by ``python -m app.cutover_check``, which the
runbook runs against the previous release before deploying this one
(``docs/operators/reliability.md`` § Cutover from the old transport). Other unpublished rows --
analytics, and flows whose work was seeded from domain state -- are dropped:
nothing reads them.

``LEMMA_CUTOVER_ACCEPT_LOSS=1`` overrides the refusal, for an install whose
operator has decided the leftovers do not matter (a development database).

The downgrade recreates both tables empty.

Revision ID: 0063_drop_event_outbox
Revises: 0062_pod_bundle_work
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0063_drop_event_outbox"
down_revision = "0062_pod_bundle_work"
branch_labels = None
depends_on = None

_CONVERTERS = (
    "changes.drain",
    "surfaces.drain_events",
    "workflow.drain_schedule_fires",
)
_OWED_TYPES = (
    "surface.webhook.received",
    "surface.onboarding.ready",
    "notification.settled",
    "schedule.fired",
)


def _never_ran_the_old_transport() -> bool:
    """A fresh install: migrations 0058-0062 queued the converters, but no
    event ever reached the outbox or inbox, so there is nothing to convert."""
    bind = op.get_bind()
    for table in ("domain_event_outbox", "domain_event_inbox"):
        if not bind.execute(
            sa.text(f"SELECT to_regclass('{table}') IS NOT NULL")
        ).scalar():
            continue
        if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            return False
    return True


def _gate_problems() -> list[str]:
    bind = op.get_bind()
    if _never_ran_the_old_transport():
        # The converters' seeded items have nothing to do and no handler left.
        bind.execute(
            sa.text(
                "DELETE FROM work_items WHERE kind = ANY(:kinds) AND state = 'queued'"
            ),
            {"kinds": list(_CONVERTERS)},
        )
    problems = []
    converters = bind.execute(
        sa.text(
            "SELECT kind, count(*) FROM work_items WHERE kind = ANY(:kinds) GROUP BY kind"
        ),
        {"kinds": list(_CONVERTERS)},
    ).all()
    problems += [
        f"converter {kind}: {count} item(s) left" for kind, count in converters
    ]
    if bind.execute(
        sa.text("SELECT to_regclass('domain_event_outbox') IS NOT NULL")
    ).scalar():
        owed = bind.execute(
            sa.text(
                "SELECT count(*) FROM domain_event_outbox "
                "WHERE published_at IS NULL AND dead_lettered_at IS NULL "
                "AND (event_type = ANY(:owed) OR event_type LIKE 'datastore.record.%')"
            ),
            {"owed": list(_OWED_TYPES)},
        ).scalar()
        if owed:
            problems.append(f"domain_event_outbox: {owed} owed row(s) not drained")
    return problems


def upgrade() -> None:
    problems = _gate_problems()
    if problems and os.environ.get("LEMMA_CUTOVER_ACCEPT_LOSS") != "1":
        raise RuntimeError(
            "the old transport is not drained; deploy the previous release, wait "
            "for `python -m app.cutover_check` to pass, then retry: "
            + "; ".join(problems)
        )
    op.execute(sa.text("DROP TABLE IF EXISTS domain_event_inbox"))
    op.execute(sa.text("DROP TABLE IF EXISTS domain_event_outbox"))


def downgrade() -> None:
    _recreate_empty()


def _recreate_empty() -> None:
    """The tables as 0003 created them, empty."""
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
