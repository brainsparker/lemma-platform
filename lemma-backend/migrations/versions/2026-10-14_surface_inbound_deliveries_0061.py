"""Surface inbound messages, deliveries, message claims and receiver state.

Surfaces move off the event stream and Redis onto the main database:

* ``surface_inbound_messages`` -- one row per inbound delivery (a webhook, a
  receiver poll), unique on ``(receiver_scope, source_event_id)``. The unique
  conflict is the dedup: durable, with no TTL, so a platform retrying for days
  (Meta retries a WhatsApp webhook for seven) is answered once. It replaces the
  Redis delivery claim and the ``surface-webhook-events`` consumer.
* ``surface_message_claims`` -- the message-level claim one inbound message
  takes once it is parsed (Slack sends ``message`` *and* ``app_mention`` for
  one message, with two event ids), and the double-tap guard on a native
  button. Owned by the inbound row that took it, so that row's own retry is
  not read as a duplicate of itself. Replaces the Redis ``event_dedup`` keys.
* ``surface_deliveries`` -- every outbound message the platform must receive
  exactly once: a run's answer, a held WhatsApp reply, a "please resend".
  ``PENDING -> SENDING -> SENT | UNKNOWN | FAILED``; a send whose outcome was
  lost is UNKNOWN and never sent again. Replaces the owed-reply metadata, its
  Redis claims and its sweep.
* ``surface_receiver_state`` -- a native receiver's cursor (Telegram offset,
  Resend cursor, Telegram manager offset), written in the transaction that
  stores what it read. Replaces the Redis keys that held them with no TTL.

What the old transport still owes the surfaces (webhooks in the outbox, owed
replies in conversation metadata, open notifications with no expiry item) is
converted by the cutover sweep that migration 0063 queues
(``app/core/infrastructure/cutover``).

Revision ID: 0061_surface_inbound_deliveries
Revises: 0060_agent_wait_approval_work
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0061_surface_inbound_deliveries"
down_revision = "0060_agent_wait_approval_work"
branch_labels = None
depends_on = None


def _now(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
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
    )
    # Retention reads finished rows by age.
    op.create_index(
        "ix_surface_inbound_messages_status_received",
        "surface_inbound_messages",
        ["status", "received_at"],
    )

    op.create_table(
        "surface_message_claims",
        sa.Column("claim_key", sa.Text(), primary_key=True),
        sa.Column("owner", sa.Text(), nullable=True),
        _now("created_at"),
    )
    op.create_index(
        "ix_surface_message_claims_created_at",
        "surface_message_claims",
        ["created_at"],
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
    )
    # The destination pump: the oldest unfinished delivery for one destination.
    op.create_index(
        "ix_surface_deliveries_destination_open",
        "surface_deliveries",
        ["destination_key", "created_at"],
        postgresql_where=sa.text("status IN ('PENDING', 'SENDING')"),
    )
    op.create_index(
        "ix_surface_deliveries_status_created",
        "surface_deliveries",
        ["status", "created_at"],
    )

    op.create_table(
        "surface_receiver_state",
        sa.Column("receiver_key", sa.Text(), primary_key=True),
        sa.Column("cursor", sa.Text(), nullable=True),
        _now("updated_at"),
    )


def downgrade() -> None:
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
