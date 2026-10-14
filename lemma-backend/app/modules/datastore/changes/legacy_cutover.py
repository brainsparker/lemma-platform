"""The cutover's converters for record changes and file cleanups the old transport owed.

Record events (``datastore.record.*``) reached DATASTORE triggers and MCP
event subscriptions through two consumer groups. Now each is a durable
subscriber of its pod's change log, so an owed record event becomes a row in
the pod's ``reserved_changes`` with the snapshot it carried -- under its own
event id, which a unique index keeps once however many routes owed it, and
which DATASTORE triggers and MCP deliveries key their own dedup on, as the old
consumers did. Appending waits until no ``changes.sync_subscribers`` item is
outstanding: migration 0058 creates the subscribers at the log's horizon, and
a change appended before its subscriber exists would be behind it.

With a separately configured datastore database the record events sat in
*its* outbox, which a main-database migration cannot read; this module
registers that outbox with the sweep (``register_legacy_outbox``), which reads
it, checks it against the inbox on the main database, and drops it once the
previous release has stopped. On a shared database the main outbox holds them.

``streaq:cleanup_deleted_datastore_paths`` -- a storage and search purge for
rows already deleted, whose paths only the Redis job held -- becomes the
``file.cleanup`` item a delete writes today.

Worker only.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.config import settings
from app.core.infrastructure.cutover.routes import legacy_converter
from app.core.infrastructure.cutover.work import LegacyOutbox, register_legacy_outbox
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import Defer, JobContext
from app.core.log.log import get_logger
from app.modules.datastore.changes.schema import (
    PENDING_CHANNEL,
    PENDING_TABLE,
    changes_table,
)
from app.modules.datastore.changes.work import SYNC_SUBSCRIBERS
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    CHANGE_LOG_VERSION,
    migrate_pod_schema,
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine

logger = get_logger(__name__)

RECORD_ROUTES = ("schedule-datastore-events", "mcp-access.record-created")
_WAIT_FOR_SYNCS = timedelta(seconds=5)
_OPS = {"INSERT": "I", "UPDATE": "U", "DELETE": "D"}


@legacy_converter(*RECORD_ROUTES)
async def record_change(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    del route
    async with ctx.checkpoint() as uow:
        if await _syncs_outstanding(uow):
            raise Defer(_WAIT_FOR_SYNCS)
        async with get_datastore_engine().begin() as conn:
            await append_events(conn, uow, [event])


async def append_events(
    conn: AsyncConnection,
    uow: SqlAlchemyUnitOfWork,
    events: Sequence[Mapping[str, Any]],
) -> int:
    """Append record events to their pods' logs; skip ones already appended."""
    from app.modules.datastore.infrastructure.repositories import (
        DatastoreTableRepository,
    )

    by_pod: dict[UUID, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        if str(event.get("event_type", "")).startswith("datastore.record."):
            by_pod[UUID(str(event["pod_id"]))].append(event)
    appended = 0
    tables = DatastoreTableRepository(uow)
    for pod_id, pod_events in by_pod.items():
        names = sorted({str(event["table_name"]) for event in pod_events})
        ids = {
            name: table.id
            for name, table in (
                await tables.get_many_by_datastore_and_names(pod_id, names)
            ).items()
        }
        schema = schema_name_for_pod(pod_id)
        version = await migrate_pod_schema(
            get_datastore_engine(), schema, target=CHANGE_LOG_VERSION, create=False
        )
        records = [_record(event, ids) for event in pod_events]
        kept = [record for record in records if record is not None]
        if version < CHANGE_LOG_VERSION or not kept:
            # The pod's schema or table is gone: nothing is left to trigger on,
            # as the old consumer would have found too.
            logger.info(
                "datastore.changes.cutover.dropped",
                pod_id=str(pod_id),
                dropped=len(pod_events),
            )
            continue
        result = await conn.execute(
            text(
                f"INSERT INTO {changes_table(schema)} (table_id, op, record_id, intent, "
                "owner_user_id, outside_actor, event_id, new_row, old_values, at) "
                "SELECT table_id, op, record_id, 'normal', owner_user_id, outside_actor, "
                "event_id, new_row, old_values, at "
                "FROM jsonb_to_recordset(CAST(:records AS jsonb)) AS r("
                "table_id uuid, op char(1), record_id text, owner_user_id uuid, "
                "outside_actor text, event_id uuid, new_row jsonb, old_values jsonb, "
                "at timestamptz) ORDER BY at "
                "ON CONFLICT (event_id) WHERE event_id IS NOT NULL DO NOTHING"
            ),
            {"records": _encode_records(kept)},
        )
        appended += int(result.rowcount or 0)
        await conn.execute(
            text(f"INSERT INTO {PENDING_TABLE} (pod_id) VALUES (CAST(:pod AS uuid))"),
            {"pod": str(pod_id)},
        )
    if appended:
        await conn.execute(text("SELECT pg_notify(:c, '')"), {"c": PENDING_CHANNEL})
    return appended


def _encode_records(records: list[dict[str, object]]) -> str:
    """On the loop: a handful of events whose payloads the outbox truncated."""
    return json.dumps(records, default=str)


def _record(
    event: Mapping[str, Any], table_ids: Mapping[str, UUID]
) -> dict[str, object] | None:
    table_id = table_ids.get(str(event.get("table_name")))
    op = _OPS.get(str(event.get("operation")))
    if table_id is None or op is None:
        return None
    payload = event.get("payload") if not event.get("payload_truncated") else None
    previous = event.get("previous") if not event.get("previous_truncated") else None
    return {
        "table_id": str(table_id),
        "op": op,
        "record_id": str(event.get("record_id")),
        "owner_user_id": event.get("owner_user_id"),
        "outside_actor": event.get("outside_actor"),
        "event_id": str(event.get("event_id")),
        "new_row": payload if op != "D" else None,
        "old_values": payload if op == "D" else (previous if op == "U" else None),
        "at": event.get("occurred_at"),
    }


async def _syncs_outstanding(uow: SqlAlchemyUnitOfWork) -> bool:
    return bool(
        await uow.session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM work_items "
                "WHERE kind = :kind AND state <> 'dead')"
            ),
            {"kind": SYNC_SUBSCRIBERS},
        )
    )


@legacy_converter("streaq:cleanup_deleted_datastore_paths")
async def file_cleanup(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    del route
    from app.modules.datastore.events.file_work import (
        CleanupFile,
        FileCleanupPayload,
        add_file_cleanup_work,
    )

    kwargs = dict(event.get("kwargs") or {})
    if not kwargs.get("pod_id"):
        return
    payload = FileCleanupPayload(
        is_folder=bool(kwargs.get("is_folder")),
        folder_prefix=kwargs.get("folder_prefix"),
        files=[
            CleanupFile.model_validate(dict(item)) for item in kwargs.get("files") or []
        ],
    )
    async with ctx.complete_work() as uow:
        await add_file_cleanup_work(
            uow, pod_id=UUID(str(kwargs["pod_id"])), payload=payload
        )


def _separate_datastore_engine() -> AsyncEngine | None:
    """The datastore database's engine when it is not the main database."""
    url = datastore_settings.datastore_database_url
    if not url or url == settings.database_url:
        return None
    return get_datastore_engine()


register_legacy_outbox(
    LegacyOutbox(
        name="datastore",
        engine=_separate_datastore_engine,
        routes=RECORD_ROUTES,
        like="datastore.record.%",
    )
)
