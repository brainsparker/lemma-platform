"""The write path: every record write appends its changes in the same statement.

A write statement that already ends in ``RETURNING`` is wrapped in a CTE that
inserts one row per written record into the pod's ``reserved_changes``:

    WITH w AS (<the write ... RETURNING *>),
         c AS (INSERT INTO reserved_changes (...) SELECT ... FROM w RETURNING ...),
         p AS (INSERT INTO lemma_change_pending (pod_id) SELECT ... WHERE <watched>)
    SELECT * FROM w

so the append costs no round trip and cannot commit apart from the write. The
row is slim (~80 bytes) unless a durable subscriber watches the table and
operation and acts on the write's intent: then it also carries the new row and
the prior values, taken from what the statement returned (the prior image the
update statements already capture), never from an extra read. Whether anything watches is one probe of
the pod's tiny subscriber table per write call.

A watched write also leaves a ``lemma_change_pending`` row and notifies the
handoff in the same transaction. After commit, every write publishes a
best-effort wake on Redis for the live socket's pod hub; losing it costs
latency, never a change, because the hub reads the log from its own cursor.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident
from app.modules.datastore.changes.schema import (
    PENDING_CHANNEL,
    PENDING_TABLE,
    changes_table,
    subscribers_table,
)
from app.modules.datastore.domain.write_intent import WriteIntent
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    ensure_change_log,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.services.table_context import TableContext

logger = get_logger(__name__)

#: Redis Pub/Sub channel a pod's live hub listens on; the payload is empty.
LIVE_CHANNEL_FORMAT = "pod:{pod_id}:changes"
#: A wake is a hint; a write never waits longer than this for Redis.
_PUBLISH_TIMEOUT_SECONDS = 0.25

type Op = Literal["I", "U", "D"]
#: How the written rows come back: every column (the caller converts them), or
#: just how many (bulk writes that only count).
type Returning = Literal["rows", "count"]

_publish_incident = DependencyIncident("datastore.changes.live_wake", logger=logger)


def live_channel(pod_id: UUID) -> str:
    return LIVE_CHANNEL_FORMAT.format(pod_id=pod_id)


def wake_payload(xid: str | None) -> str:
    """``{"xid": "<xid8>"}``: the write's transaction. A change it wrote is
    readable once a read's horizon is past it, so a hub can wait for that
    instead of re-reading an empty page while an older transaction is open."""
    return json.dumps({"xid": xid}) if xid is not None else ""


def parse_wake(payload: str | bytes) -> int | None:
    """The transaction id a wake names, or ``None`` (an older, empty wake)."""
    try:
        value = json.loads(payload or b"{}")
    except ValueError:
        return None
    xid = value.get("xid") if isinstance(value, dict) else None
    return int(xid) if isinstance(xid, str) and xid.isdigit() else None


def _free_alias(taken: set[str], base: str) -> str:
    """An output column name no table column can shadow."""
    alias = base
    while alias in taken:
        alias += "_"
    return alias


@dataclass(slots=True)
class ChangeCapture:
    """One write call's appends: built before the transaction, used inside it."""

    ctx: TableContext
    intent: WriteIntent = WriteIntent.NORMAL
    outside_actor: str | None = None
    #: Operations a durable subscriber watches on this table, as of ``begin``.
    watched: frozenset[str] = field(default_factory=frozenset)
    #: Of those, the ones some subscriber acts on for this write's intent:
    #: only these changes carry their rows. A bulk write every watcher skips
    #: is logged slim and still wakes them, so they can record the skip.
    snapshot: frozenset[str] = field(default_factory=frozenset)
    _statements: int = 0
    #: The writing transaction's id (xid8, as text), once ``wake_handoff`` ran.
    xid: str | None = None

    async def prepare(self) -> None:
        """Make sure the pod schema has a change log; a memo hit after the first."""
        await ensure_change_log(get_datastore_engine(), self.ctx.schema_name)

    async def begin(self, session: AsyncSession) -> None:
        """Ask, in the write's transaction, what is watched on this table."""
        watched, snapshot = (
            await session.execute(
                text(
                    "SELECT array_agg(DISTINCT op), array_agg(DISTINCT op) "
                    "FILTER (WHERE :intent = ANY(snapshot_intents)) FROM "
                    f"{subscribers_table(self.ctx.schema_name)}, unnest(ops) AS op "
                    "WHERE table_id = CAST(:table_id AS uuid)"
                ),
                {"table_id": str(self.ctx.table_id), "intent": self.intent.value},
            )
        ).one()
        self.watched = frozenset(str(op) for op in watched or ())
        self.snapshot = frozenset(str(op) for op in snapshot or ())

    def wrap(
        self,
        sql: str,
        params: dict[str, object],
        *,
        op: Op | None,
        previous_alias: str | None = None,
        changed: list[str] | None = None,
        returning: Returning = "rows",
    ) -> tuple[str, dict[str, object]]:
        """``sql`` (ending in ``RETURNING ...``) with its changes appended.

        ``op`` ``None`` is an upsert: each row is an insert or an update by
        whether the statement inserted it (``xmax = 0``). ``previous_alias``
        names the column carrying the prior row as JSON text (updates), and
        ``changed`` the columns the update wrote.
        """
        ctx = self.ctx
        taken = {column.name for column in ctx.columns} | {ctx.primary_key_column}
        drop = [previous_alias] if previous_alias else []
        if op is None:
            inserted = _free_alias(taken, "__lemma_inserted")
            sql = f'{sql}, (xmax = 0) AS "{inserted}"'
            drop.append(inserted)
            op_sql = f"CASE WHEN w.\"{inserted}\" THEN 'I' ELSE 'U' END"
        else:
            op_sql = f"'{op}'"
        row_json = "to_jsonb(w)"
        if drop:
            row_json = "(to_jsonb(w) - CAST(:__chg_drop AS text[]))"
        new_row = f"CASE WHEN o.op <> 'D' THEN {row_json} END"
        if previous_alias is not None and changed:
            prior = (
                '(SELECT jsonb_object_agg(k, CAST(w."{alias}" AS jsonb) -> k) '
                "FROM unnest(CAST(:__chg_changed AS text[])) AS k)"
            ).format(alias=previous_alias)
            old_values = f"CASE WHEN o.op = 'D' THEN {row_json} ELSE {prior} END"
        else:
            old_values = f"CASE WHEN o.op = 'D' THEN {row_json} END"
        owner = 'CAST(w."user_id" AS uuid)' if ctx.enable_rls else "NULL::uuid"
        watched = "o.op = ANY(CAST(:__chg_watched AS text[]))"
        snapshot = "o.op = ANY(CAST(:__chg_snapshot AS text[]))"
        statement = (
            f"WITH w AS ({sql}), "
            f"c AS (INSERT INTO {changes_table(ctx.schema_name)} "
            "(table_id, op, record_id, intent, owner_user_id, outside_actor, "
            "new_row, old_values) "
            f"SELECT CAST(:__chg_table AS uuid), o.op, "
            f'CAST(w."{ctx.primary_key_column}" AS text), :__chg_intent, {owner}, '
            f"CAST(:__chg_outside AS text), "
            f"CASE WHEN {snapshot} THEN {new_row} END, "
            f"CASE WHEN {snapshot} THEN {old_values} END "
            f"FROM w CROSS JOIN LATERAL (SELECT {op_sql} AS op) AS o "
            f"RETURNING {watched.replace('o.op', 'op')} AS watched)"
        )
        if self.watched:
            statement += (
                f", p AS (INSERT INTO {PENDING_TABLE} (pod_id) "
                "SELECT CAST(:__chg_pod AS uuid) "
                "WHERE EXISTS (SELECT 1 FROM c WHERE c.watched))"
            )
        statement += (
            " SELECT * FROM w" if returning == "rows" else " SELECT count(*) FROM w"
        )
        bound: dict[str, object] = {
            **params,
            "__chg_table": str(ctx.table_id),
            "__chg_intent": self.intent.value,
            "__chg_outside": self.outside_actor,
            "__chg_watched": sorted(self.watched),
            "__chg_snapshot": sorted(self.snapshot),
        }
        if drop:
            bound["__chg_drop"] = drop
        if previous_alias is not None and changed:
            bound["__chg_changed"] = list(changed)
        if self.watched:
            bound["__chg_pod"] = str(ctx.pod_id)
        self._statements += 1
        return statement, bound

    async def wake_handoff(self, session: AsyncSession) -> None:
        """Before commit: note this transaction's id, and wake the handoff if
        anything here was watched -- one round trip either way."""
        if not self._statements:
            return
        notify = "pg_notify(:channel, '')" if self.watched else "NULL"
        self.xid = await session.scalar(
            text(f"SELECT pg_current_xact_id()::text, {notify}"),
            {"channel": PENDING_CHANNEL},
        )

    async def publish_wake(self) -> None:
        """After commit: tell this pod's live hubs to read. Best effort."""
        if not self._statements:
            return
        try:
            client = get_redis(url=settings.redis_url)
            await asyncio.wait_for(
                client.publish(live_channel(self.ctx.pod_id), wake_payload(self.xid)),
                timeout=_PUBLISH_TIMEOUT_SECONDS,
            )
        except (RedisError, OSError, TimeoutError) as exc:
            # The log already holds the change; a hub that misses this wake
            # reads it on its next one, or on its fallback poll.
            _publish_incident.record_failure(error_type=type(exc).__name__)
        else:
            _publish_incident.record_success()


__all__ = [
    "LIVE_CHANNEL_FORMAT",
    "ChangeCapture",
    "live_channel",
    "parse_wake",
    "wake_payload",
]
