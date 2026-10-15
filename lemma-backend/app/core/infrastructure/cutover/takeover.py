"""Take over what the old transport owes: settle its inbox row, write a work item.

For each route (``routes.py``), in one statement: select the outbox rows owed
to that consumer, mark the consumer's inbox row for each ``COMPLETED`` (marked
``TAKEN_OVER_MARK``), and write one ``cutover.legacy_event`` item per row the
mark actually took. The inbox row is the old consumer's own dedup, so an old
worker still running during a rolling deploy skips a delivery once it is taken
over, and a delivery an old worker claimed in the last minute (a live
``PROCESSING`` claim) is left to it. Run twice, the second pass finds the
first's marks and takes nothing.

The same statement runs over rows from a separately configured datastore
database (``take_over_rows``): its outbox holds the record events, while the
inbox they are checked against is on the main database.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.cutover.routes import (
    ROUTES,
    TAKEN_OVER_MARK,
    LegacyRoute,
)

LEGACY_EVENT = "cutover.legacy_event"
LEGACY_SWEEP = "cutover.legacy_sweep"
#: Where converted deliveries queue, and the per-namespace cap they start with.
LEGACY_NS = "system"
LEGACY_CLASS = "events"
LEGACY_CLASS_CAP = 16

#: A claim younger than this belongs to a live old consumer (the inbox's own
#: redelivery window).
_LIVE_CLAIM = "interval '60 seconds'"
_SETTLED = "('COMPLETED', 'TERMINAL', 'DEAD_LETTER')"

ENSURE_QUEUE = text(
    "INSERT INTO work_queues (class, ns, max_running) "
    f"VALUES ('{LEGACY_CLASS}', '{LEGACY_NS}', {LEGACY_CLASS_CAP}) "
    "ON CONFLICT (class, ns) DO NOTHING"
)


def _matches(route: LegacyRoute) -> str:
    clauses = []
    if route.types:
        clauses.append("o.event_type = ANY(CAST(:types AS text[]))")
    if route.like:
        clauses.append("o.event_type LIKE CAST(:like AS text)")
    match = " OR ".join(clauses) or "false"
    return f"({match})" + (f" AND {route.where}" if route.where else "")


def owed_predicate(route: LegacyRoute) -> str:
    """SQL on outbox row ``o``: owed to ``route.consumer`` (``:consumer``)."""
    # A dead-lettered row never reached any consumer: the old publisher gave
    # up on it and only an operator's replay sent it again. It stays dead.
    return (
        f"o.dead_lettered_at IS NULL AND {_matches(route)} AND NOT EXISTS ("
        "SELECT 1 FROM domain_event_inbox i "
        "WHERE i.consumer = CAST(:consumer AS text) AND i.event_id = o.id "
        f"AND (i.status IN {_SETTLED} OR (i.status = 'PROCESSING' "
        f"AND i.last_received_at > now() - {_LIVE_CLAIM})))"
    )


def _take_over(route: LegacyRoute, source: str, *, since: bool = False) -> str:
    """``source`` yields ``o(id, event_type, payload, occurred_at)``;
    ``since``: only rows that occurred from ``:since`` on."""
    window = "AND o.occurred_at >= CAST(:since AS timestamptz)" if since else ""
    return f"""
WITH owed AS (
    SELECT o.id, o.event_type, o.payload, o.occurred_at FROM {source}
    WHERE {owed_predicate(route)} {window}
    ORDER BY o.occurred_at, o.id
    LIMIT CAST(:limit AS integer)
), taken AS (
    INSERT INTO domain_event_inbox (
        id, consumer, event_id, event_type, status, attempts, delivery_count,
        first_received_at, last_received_at, completed_at, last_error_type,
        created_at)
    SELECT gen_random_uuid(), CAST(:consumer AS text), owed.id, owed.event_type, 'COMPLETED',
           0, 0, owed.occurred_at, owed.occurred_at, now(), '{TAKEN_OVER_MARK}',
           now()
      FROM owed
    ON CONFLICT (consumer, event_id) DO UPDATE
       SET status = 'COMPLETED', completed_at = now(),
           last_error_type = '{TAKEN_OVER_MARK}'
     WHERE domain_event_inbox.status NOT IN {_SETTLED}
       AND NOT (domain_event_inbox.status = 'PROCESSING'
                AND domain_event_inbox.last_received_at > now() - {_LIVE_CLAIM})
    RETURNING event_id
)
INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at)
SELECT gen_random_uuid(), '{LEGACY_NS}', '{LEGACY_CLASS}', '{LEGACY_EVENT}',
       jsonb_build_object('route', CAST(:consumer AS text), 'event', owed.payload),
       'legacy:' || CAST(:consumer AS text) || ':' || owed.id::text, now()
  FROM owed JOIN taken ON taken.event_id = owed.id
ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' DO NOTHING
"""


def _params(route: LegacyRoute, limit: int) -> dict[str, Any]:
    params: dict[str, Any] = {"consumer": route.consumer, "limit": limit}
    if route.types:
        params["types"] = list(route.types)
    if route.like:
        params["like"] = route.like
    return params


async def take_over(
    session: AsyncSession,
    *,
    routes: Sequence[LegacyRoute] = ROUTES,
    limit: int = 5_000,
    since: datetime | None = None,
) -> dict[str, int]:
    """One batch per route from the main outbox; items written per route.

    ``since`` narrows the scan to rows that occurred from then on: after a
    full pass, what can newly become owed is what the previous release is
    still writing. (A row a live old worker had claimed becomes owed only if
    that worker died with it; the final pass, which is full, takes those.)
    Runs on ``session``'s transaction; the caller commits.
    """
    await session.execute(ENSURE_QUEUE)
    taken: dict[str, int] = {}
    for route in routes:
        params = _params(route, limit)
        if since is not None:
            params["since"] = since
        result = await session.execute(
            text(_take_over(route, "domain_event_outbox o", since=since is not None)),
            params,
        )
        count = int(getattr(result, "rowcount", 0) or 0)
        if count:
            taken[route.consumer] = count
    return taken


async def take_over_rows(
    session: AsyncSession, route: LegacyRoute, rows: Sequence[dict[str, Any]]
) -> int:
    """Take over ``rows`` (``id``, ``event_type``, ``payload``, ``occurred_at``)
    read from another database's outbox, checked against this one's inbox."""
    if not rows:
        return 0
    await session.execute(ENSURE_QUEUE)
    source = (
        "jsonb_to_recordset(CAST(:rows AS jsonb)) "
        "AS o(id uuid, event_type text, payload jsonb, occurred_at timestamptz, "
        "dead_lettered_at timestamptz)"
    )
    result = await session.execute(
        text(_take_over(route, source)),
        {**_params(route, len(rows)), "rows": _encode(rows)},
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def count_owed(session: AsyncSession, route: LegacyRoute) -> int:
    """How many outbox rows are owed to ``route`` right now (for the check)."""
    value = await session.scalar(
        text(
            f"SELECT count(*) FROM domain_event_outbox o WHERE {owed_predicate(route)}"
        ),
        {k: v for k, v in _params(route, 0).items() if k != "limit"},
    )
    return int(value or 0)


def _encode(rows: Sequence[dict[str, Any]]) -> str:
    """On the loop: one bounded batch of already-truncated event payloads."""
    return json.dumps(list(rows), default=str)


__all__ = [
    "ENSURE_QUEUE",
    "LEGACY_CLASS",
    "LEGACY_EVENT",
    "LEGACY_NS",
    "LEGACY_SWEEP",
    "count_owed",
    "owed_predicate",
    "take_over",
    "take_over_rows",
]
