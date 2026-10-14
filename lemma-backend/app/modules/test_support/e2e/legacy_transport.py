"""The old event transport's tables, as a database left them, for cutover tests.

``domain_event_outbox`` and ``domain_event_inbox`` exactly as migration 0003
created them (and 0063's downgrade recreates them), plus helpers that write
rows the way the old unit of work, publisher and inbox did.
"""

from __future__ import annotations

import json
import pickle  # noqa: S403 - writes the shape streaq stored, for tests
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS domain_event_outbox (
    stream varchar(160) NOT NULL,
    event_type varchar(200) NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    producer varchar(120) NOT NULL,
    payload jsonb NOT NULL,
    occurred_at timestamptz NOT NULL,
    correlation_id uuid,
    causation_id uuid,
    request_id varchar(160),
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamptz NOT NULL,
    lease_until timestamptz,
    lease_owner varchar(160),
    published_at timestamptz,
    dead_lettered_at timestamptz,
    last_error_type varchar(200),
    last_error text,
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_domain_event_outbox_occurred
    ON domain_event_outbox (occurred_at DESC, id DESC)
"""
INBOX_DDL = """
CREATE TABLE IF NOT EXISTS domain_event_inbox (
    consumer varchar(200) NOT NULL,
    event_id uuid NOT NULL,
    event_type varchar(200) NOT NULL,
    status varchar(32) DEFAULT 'PROCESSING' NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    delivery_count integer DEFAULT 0 NOT NULL,
    first_received_at timestamptz NOT NULL,
    last_received_at timestamptz NOT NULL,
    completed_at timestamptz,
    dead_lettered_at timestamptz,
    last_error_type varchar(200),
    last_error text,
    id uuid PRIMARY KEY,
    created_at timestamptz NOT NULL,
    CONSTRAINT uq_domain_event_inbox_consumer_event UNIQUE (consumer, event_id)
);
CREATE INDEX IF NOT EXISTS ix_domain_event_inbox_status_received
    ON domain_event_inbox (status, last_received_at)
"""


async def create_legacy_tables(conn: AsyncConnection, *, inbox: bool = True) -> None:
    for ddl in (OUTBOX_DDL, *((INBOX_DDL,) if inbox else ())):
        for statement in ddl.split(";"):
            if statement.strip():
                await conn.execute(text(statement))


def event(event_type: str, **fields: Any) -> dict[str, Any]:
    """An event as the old unit of work dumped it (``model_dump(mode="json")``)."""
    return {
        "event_id": str(uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(UTC).isoformat(),
        "schema_version": 1,
        **{k: (str(v) if isinstance(v, UUID) else v) for k, v in fields.items()},
    }


async def outbox_row(
    conn: AsyncConnection,
    payload: dict[str, Any],
    *,
    stream: str = "legacy_events",
    published: bool = False,
    dead: bool = False,
    occurred_at: datetime | None = None,
) -> UUID:
    """One outbox row for ``payload`` (its ``event_id`` is the row id)."""
    row_id = UUID(str(payload["event_id"]))
    await conn.execute(
        text(
            "INSERT INTO domain_event_outbox (id, stream, event_type, producer, "
            "payload, occurred_at, available_at, published_at, dead_lettered_at, "
            "created_at) VALUES (:id, :stream, :type, 'test', CAST(:payload AS jsonb), "
            ":at, :at, CASE WHEN :published THEN now() END, "
            "CASE WHEN :dead THEN now() END, now())"
        ),
        {
            "id": row_id,
            "stream": stream,
            "type": payload["event_type"],
            "payload": json.dumps(payload),
            "at": occurred_at or datetime.now(UTC),
            "published": published,
            "dead": dead,
        },
    )
    return row_id


async def inbox_row(
    conn: AsyncConnection,
    consumer: str,
    event_id: UUID,
    event_type: str,
    *,
    status: str = "COMPLETED",
    mark: str | None = None,
    received_at: datetime | None = None,
) -> None:
    """A consumer's inbox row, as the old inbox (or a release's mark) left it."""
    at = received_at or datetime.now(UTC)
    await conn.execute(
        text(
            "INSERT INTO domain_event_inbox (id, consumer, event_id, event_type, "
            "status, first_received_at, last_received_at, completed_at, "
            "last_error_type, created_at) VALUES (gen_random_uuid(), :consumer, "
            ":event_id, :type, :status, :at, :at, "
            "CASE WHEN :status = 'COMPLETED' THEN :at END, :mark, now())"
        ),
        {
            "consumer": consumer,
            "event_id": event_id,
            "type": event_type,
            "status": status,
            "at": at,
            "mark": mark,
        },
    )


def streaq_job(function: str, **kwargs: Any) -> bytes:
    """A queued job's data as streaq pickled it."""
    return pickle.dumps({"f": function, "a": (), "k": kwargs})


__all__ = [
    "INBOX_DDL",
    "OUTBOX_DDL",
    "create_legacy_tables",
    "event",
    "inbox_row",
    "outbox_row",
    "streaq_job",
]
