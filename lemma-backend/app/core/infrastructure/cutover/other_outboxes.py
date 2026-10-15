"""Other databases' ``domain_event_outbox``: taken over by the sweep, dropped by ``finish``.

A module whose events had an outbox of their own (the datastore database, when
it is configured separately) registers it here. Its rows are read from that
database by a cursor and taken over against the main database's inbox
(``takeover.take_over_rows``).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.infrastructure.cutover.routes import ROUTES
from app.core.infrastructure.cutover.takeover import BATCH, take_over_rows
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: A new session per call, each its own transaction (the caller's commit or
#: rollback on exit is a no-op after ours).
Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]
#: Postgres's ``lock_not_available``: a ``lock_timeout`` ran out.
LOCK_NOT_AVAILABLE = "55P03"


@dataclass(frozen=True, slots=True)
class LegacyOutbox:
    """Another database's ``domain_event_outbox``, and the routes it feeds."""

    name: str
    #: The database's engine, or ``None`` when it is the main database (whose
    #: outbox the main take-over already reads).
    engine: Callable[[], AsyncEngine | None]
    routes: tuple[str, ...]
    like: str


_other_outboxes: dict[str, LegacyOutbox] = {}  # memory: bounded one per database


def register_legacy_outbox(outbox: LegacyOutbox) -> None:
    """A module whose events had an outbox of their own (the datastore database)."""
    _other_outboxes[outbox.name] = outbox


def lock_not_available(exc: DBAPIError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE


async def sweep_other_outboxes(
    sessions: Sessions, cursors: dict[str, list[str]]
) -> tuple[int, datetime | None, bool]:
    """Take over each other outbox from its cursor on: (taken, the newest row,
    whether any such outbox still exists). Advances ``cursors``."""
    taken, newest, present = 0, None, False
    for outbox in _other_outboxes.values():
        engine = outbox.engine()
        if engine is None:
            continue
        async with engine.connect() as conn:
            if not await conn.scalar(
                text("SELECT to_regclass('domain_event_outbox') IS NOT NULL")
            ):
                continue
            last = await conn.scalar(
                text("SELECT max(occurred_at) FROM domain_event_outbox")
            )
        present = True
        newest = last if newest is None or (last and last > newest) else newest
        while taken < BATCH:
            async with engine.connect() as conn:
                rows = await _outbox_rows_after(conn, outbox, cursors.get(outbox.name))
            if not rows:
                break
            taken += await _take_over_other(sessions, outbox, rows)
            cursors[outbox.name] = _cursor(rows[-1])
    return taken, newest, present


async def drop_other_outboxes(
    sessions: Sessions, cursors: dict[str, list[str]], lock_timeout_ms: int
) -> tuple[int, tuple[str, ...]]:
    """Take over the rest of each other outbox under its lock, and drop it:
    (taken, the outboxes whose lock was not granted in time)."""
    taken, left = 0, []
    for outbox in _other_outboxes.values():
        engine = outbox.engine()
        if engine is None:
            continue
        try:
            async with engine.begin() as conn:
                if not await conn.scalar(
                    text("SELECT to_regclass('domain_event_outbox') IS NOT NULL")
                ):
                    continue
                await conn.execute(
                    text(f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms'")
                )
                await conn.execute(
                    text("LOCK TABLE domain_event_outbox IN ACCESS EXCLUSIVE MODE")
                )
                # Under the lock, read through this connection: nothing is
                # written between the last take-over and the drop.
                while rows := await _outbox_rows_after(
                    conn, outbox, cursors.get(outbox.name)
                ):
                    taken += await _take_over_other(sessions, outbox, rows)
                    cursors[outbox.name] = _cursor(rows[-1])
                await conn.execute(text("DROP TABLE domain_event_outbox"))
        except DBAPIError as exc:
            if not lock_not_available(exc):
                raise
            logger.warning("cutover.finish.outbox_lock_timeout", database=outbox.name)
            left.append(outbox.name)
            continue
        logger.info("cutover.finish.dropped_outbox", database=outbox.name)
    return taken, tuple(left)


async def _take_over_other(
    sessions: Sessions, outbox: LegacyOutbox, rows: list[dict[str, Any]]
) -> int:
    routes = {route.consumer: route for route in ROUTES}
    taken = 0
    async with sessions() as session:
        for name in outbox.routes:
            taken += await take_over_rows(session, routes[name], rows)
        await session.commit()
    return taken


def _cursor(row: dict[str, Any]) -> list[str]:
    return [row["occurred_at"].isoformat(), str(row["id"])]


async def _outbox_rows_after(
    conn: Any, outbox: LegacyOutbox, cursor: list[str] | None
) -> list[dict[str, Any]]:
    after_at, after_id = (
        (datetime.fromisoformat(cursor[0]), UUID(cursor[1]))
        if cursor
        else (datetime(1970, 1, 1, tzinfo=UTC), UUID(int=0))
    )
    result = await conn.execute(
        text(
            "SELECT id, event_type, payload, occurred_at FROM domain_event_outbox "
            "WHERE dead_lettered_at IS NULL AND event_type LIKE :like "
            "AND (occurred_at, id) > (:after_at, :after_id) "
            "ORDER BY occurred_at, id LIMIT :limit"
        ),
        {
            "like": outbox.like,
            "after_at": after_at,
            "after_id": after_id,
            "limit": BATCH,
        },
    )
    return [dict(row._mapping) for row in result]


__all__ = [
    "LOCK_NOT_AVAILABLE",
    "LegacyOutbox",
    "Sessions",
    "drop_other_outboxes",
    "lock_not_available",
    "register_legacy_outbox",
    "sweep_other_outboxes",
]
