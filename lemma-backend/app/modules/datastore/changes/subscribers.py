"""Durable subscribers: who watches a pod's changes, and how far each has read.

A durable subscriber is named ``<prefix>:<id>`` (``schedule:<uuid>``,
``mcp:<public id>``). Its module registers one :class:`DurableSubscriber` per
prefix: the change log asks it what the subscriber watches (``spec``) and hands
it pages of changes (``deliver``). Everything else -- the subscriber row, the
cursor, waking, paging, retention -- is the change log's.

The rows live in the pod's ``reserved_change_subscribers`` and are written only
by ``changes.sync_subscribers`` (and by delivery, which deletes the row of a
subscriber its module says is gone), so the table cannot drift from the
modules' own state for longer than one sync.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.changes.cursor import ChangePosition
from app.modules.datastore.changes.reader import Change, ChangeOp
from app.modules.datastore.changes.schema import changes_table, subscribers_table
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine


class DeliverOutcome(StrEnum):
    #: The page's effects are committed: move the cursor past it.
    ADVANCE = "advance"
    #: Not now (the subscriber is paused): keep the cursor and the changes.
    HOLD = "hold"
    #: The subscriber no longer exists: drop its row.
    GONE = "gone"


@dataclass(frozen=True, slots=True)
class SubscriberSpec:
    """What a subscriber watches, as its module states it."""

    table_name: str
    ops: frozenset[ChangeOp]
    #: How long a wake waits before delivering, so a batching subscriber reads
    #: what accumulated in the window instead of one change at a time.
    delay: timedelta = field(default=timedelta(0))


class DurableSubscriber(Protocol):
    async def spec(
        self, uow: SqlAlchemyUnitOfWork, pod_id: UUID, subscriber_id: str
    ) -> SubscriberSpec | None:
        """What ``subscriber_id`` watches now; ``None`` when it watches nothing."""
        ...

    async def deliver(
        self,
        uow: SqlAlchemyUnitOfWork,
        pod_id: UUID,
        subscriber_id: str,
        changes: Sequence[Change],
    ) -> DeliverOutcome:
        """Act on one page, writing every effect on ``uow`` without committing.

        Each effect must be keyed on the change (``chg:<xid>:<seq>``, or the
        converted event's id) so a page delivered twice -- after a crash
        between the commit and the cursor move -- changes nothing.
        """
        ...


_registry: dict[str, DurableSubscriber] = {}  # memory: bounded one per module prefix, at import


def register_durable_subscriber(prefix: str, subscriber: DurableSubscriber) -> None:
    if ":" in prefix:
        raise ValueError(f"subscriber prefix may not contain ':': {prefix!r}")
    existing = _registry.get(prefix)
    if existing is not None and existing is not subscriber:
        raise ValueError(f"durable subscriber {prefix!r} is already registered")
    _registry[prefix] = subscriber


def durable_subscriber(key: str) -> tuple[DurableSubscriber, str] | None:
    """The registered subscriber for ``<prefix>:<id>``, and the id."""
    prefix, sep, subscriber_id = key.partition(":")
    subscriber = _registry.get(prefix)
    if not sep or subscriber is None:
        return None
    return subscriber, subscriber_id


def subscriber_key(prefix: str, subscriber_id: object) -> str:
    return f"{prefix}:{subscriber_id}"


@dataclass(frozen=True, slots=True)
class SubscriberRow:
    subscriber: str
    table_id: UUID
    ops: frozenset[ChangeOp]
    delay: timedelta
    cursor: ChangePosition


class SubscriberTable:
    """``reserved_change_subscribers`` of one pod, in the datastore database."""

    def __init__(self, pod_id: UUID, *, engine: AsyncEngine | None = None) -> None:
        self._table = subscribers_table(schema_name_for_pod(pod_id))
        self._changes = changes_table(schema_name_for_pod(pod_id))
        self._engine = engine or get_datastore_engine()

    async def upsert(
        self,
        key: str,
        *,
        table_id: UUID,
        ops: frozenset[ChangeOp],
        delay: timedelta,
        since: datetime | None = None,
    ) -> None:
        """Create the row, or update what it watches.

        A new row starts at the first change to its table written at or after
        ``since`` -- when the subscriber was asked for, in its module's
        transaction -- or at the current horizon, whichever is earlier, so a
        write that lands between a trigger being saved and this sync running
        is not missed. A row that changes table starts again the same way: it
        is a new subscription to a different stream of changes. One that only
        changes operations keeps its cursor.
        """
        async with self._engine.begin() as conn:
            start = await self._starting_position(conn, table_id, since)
            await conn.execute(
                text(
                    f"INSERT INTO {self._table} AS s "
                    "(subscriber, table_id, ops, delay_ms, cursor_xid, cursor_seq) "
                    "VALUES (:key, CAST(:table_id AS uuid), CAST(:ops AS text[]), "
                    ":delay_ms, CAST(CAST(:xid AS text) AS xid8), :seq) "
                    "ON CONFLICT (subscriber) DO UPDATE SET "
                    "ops = EXCLUDED.ops, delay_ms = EXCLUDED.delay_ms, "
                    "updated_at = now(), "
                    "cursor_xid = CASE WHEN s.table_id = EXCLUDED.table_id "
                    "THEN s.cursor_xid ELSE EXCLUDED.cursor_xid END, "
                    "cursor_seq = CASE WHEN s.table_id = EXCLUDED.table_id "
                    "THEN s.cursor_seq ELSE EXCLUDED.cursor_seq END, "
                    "table_id = EXCLUDED.table_id"
                ),
                {
                    "key": key,
                    "table_id": str(table_id),
                    "ops": sorted(op.value for op in ops),
                    "delay_ms": int(delay.total_seconds() * 1000),
                    "xid": str(start.xid),
                    "seq": start.seq,
                },
            )

    async def _starting_position(
        self, conn: AsyncConnection, table_id: UUID, since: datetime | None
    ) -> ChangePosition:
        xmin = await conn.scalar(
            text("SELECT pg_snapshot_xmin(pg_current_snapshot())::text")
        )
        horizon = ChangePosition(int(xmin or 0), 0)
        if since is None:
            return horizon
        first = (
            await conn.execute(
                text(
                    f"SELECT xid::text, seq FROM {self._changes} "
                    "WHERE table_id = CAST(:table_id AS uuid) AND at >= :since "
                    "ORDER BY xid, seq LIMIT 1"
                ),
                {"table_id": str(table_id), "since": since},
            )
        ).one_or_none()
        if first is None:
            return horizon
        return min(horizon, ChangePosition(int(first[0]), int(first[1]) - 1))

    async def delete(self, key: str) -> None:
        """Drop the row; a schema without a change log has none to drop."""
        async with self._engine.begin() as conn:
            if not await self._exists(conn):
                return
            await conn.execute(
                text(f"DELETE FROM {self._table} WHERE subscriber = :key"), {"key": key}
            )

    async def _exists(self, conn: AsyncConnection) -> bool:
        return bool(
            await conn.scalar(
                text("SELECT to_regclass(:name) IS NOT NULL"), {"name": self._table}
            )
        )

    async def get(self, key: str) -> SubscriberRow | None:
        rows = await self._select("WHERE subscriber = :key", {"key": key})
        return rows[0] if rows else None

    async def all(self) -> list[SubscriberRow]:
        return await self._select("", {})

    async def advance(self, key: str, position: ChangePosition) -> None:
        """Move the cursor forward to ``position``; never backward."""
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    f"UPDATE {self._table} SET "
                    "cursor_xid = CAST(CAST(:xid AS text) AS xid8), cursor_seq = :seq, "
                    "updated_at = now() WHERE subscriber = :key AND "
                    "(cursor_xid, cursor_seq) < (CAST(CAST(:xid AS text) AS xid8), :seq)"
                ),
                {"key": key, "xid": str(position.xid), "seq": position.seq},
            )

    async def _select(
        self, where: str, params: dict[str, object]
    ) -> list[SubscriberRow]:
        async with self._engine.connect() as conn:
            if not await self._exists(conn):
                return []
            rows = (
                await conn.execute(
                    text(
                        "SELECT subscriber, table_id, ops, delay_ms, "
                        f"cursor_xid::text, cursor_seq FROM {self._table} {where} "
                        "ORDER BY subscriber"
                    ),
                    params,
                )
            ).all()
        return [
            SubscriberRow(
                subscriber=str(row[0]),
                table_id=UUID(str(row[1])),
                ops=frozenset(ChangeOp(str(op)) for op in row[2]),
                delay=timedelta(milliseconds=int(row[3])),
                cursor=ChangePosition(int(row[4]), int(row[5])),
            )
            for row in rows
        ]


__all__ = [
    "DeliverOutcome",
    "DurableSubscriber",
    "SubscriberRow",
    "SubscriberSpec",
    "SubscriberTable",
    "durable_subscriber",
    "register_durable_subscriber",
    "subscriber_key",
]
