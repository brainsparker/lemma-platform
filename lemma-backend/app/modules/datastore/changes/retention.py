"""``changes.retention``: keep a pod's log to the catch-up window, and never less.

A change is deleted only when it is older than the window **and** every durable
subscriber of its pod has read it. A pod with no subscribers keeps the window's
slim rows for live catch-up; a pod with a stuck subscriber grows its own log --
visible as that pod's lag -- and costs no other pod anything.

The newest deleted position becomes the pod's floor: a live reader whose
cursor is below it may have missed changes and is told to resync.

The same pass looks after the subscribers: a row its module no longer
recognises is dropped (a subscription that lapsed without a sync), and a
subscriber with readable changes and no wake gets a delivery -- the backstop
for a wake lost between a write and a subscriber's creation.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text

from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.datastore.changes.cursor import encode_cursor
from app.modules.datastore.changes.reader import read_after
from app.modules.datastore.changes.schema import FLOORS_TABLE, changes_table
from app.modules.datastore.changes.subscribers import (
    SubscriberRow,
    SubscriberTable,
    durable_subscriber,
)
from app.modules.datastore.changes.work import request_delivery
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    CHANGE_LOG_VERSION,
    VERSIONS_TABLE,
    pod_id_for_schema,
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine

logger = get_logger(__name__)

RETENTION = "changes.retention"
RETENTION_EVERY = timedelta(hours=1)
#: Pod schemas per item; the next page is its own item.
RETENTION_PAGE = 200
#: Rows per delete statement, so one pod's backlog is never one long statement.
DELETE_CHUNK = 50_000


class RetentionPage(BaseModel):
    after: str = ""


async def run_retention(ctx: JobContext, payload: RetentionPage) -> None:
    schemas = await _schemas_with_log(payload.after)
    deleted = 0
    for schema in schemas:
        ctx.raise_if_stopping()
        pod_id = UUID(pod_id_for_schema(schema))
        subscribers = await _live_subscribers(ctx, pod_id)
        deleted += await retain(pod_id, subscribers)
        async with ctx.checkpoint() as uow:
            work = ctx.writer(uow)
            for subscriber in subscribers:
                if await _behind(pod_id, subscriber):
                    await request_delivery(work, pod_id, subscriber.subscriber)
    async with ctx.complete_work() as uow:
        if len(schemas) == RETENTION_PAGE:
            await ctx.writer(uow).add(
                RETENTION,
                SYSTEM_NAMESPACE,
                RetentionPage(after=schemas[-1]),
                dedup_key=f"changes.retention:{schemas[-1]}",
            )
    if deleted:
        logger.info("datastore.changes.retention.observed", deleted=deleted)


async def retain(pod_id: UUID, subscribers: list[SubscriberRow]) -> int:
    """Delete what this pod's log no longer needs; how many rows went."""
    engine = get_datastore_engine()
    table = changes_table(schema_name_for_pod(pod_id))
    window = timedelta(seconds=datastore_settings.datastore_change_retention_seconds)
    where = ["at < now() - make_interval(secs => :window)"]
    params: dict[str, object] = {
        "window": window.total_seconds(),
        "limit": DELETE_CHUNK,
        "pod_id": str(pod_id),
    }
    if subscribers:
        read = min(subscriber.cursor for subscriber in subscribers)
        where.append("(xid, seq) <= (CAST(CAST(:xid AS text) AS xid8), :seq)")
        params.update(xid=str(read.xid), seq=read.seq)
    total = 0
    while True:
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        f"WITH gone AS (DELETE FROM {table} WHERE (xid, seq) IN ("
                        f"SELECT xid, seq FROM {table} WHERE {' AND '.join(where)} "
                        "ORDER BY xid, seq LIMIT :limit) RETURNING xid, seq), "
                        "top AS (SELECT xid, seq FROM gone ORDER BY xid DESC, seq DESC "
                        "LIMIT 1), "
                        f"floor AS (INSERT INTO {FLOORS_TABLE} AS f "
                        "(pod_id, floor_xid, floor_seq) "
                        "SELECT CAST(:pod_id AS uuid), xid, seq FROM top "
                        "ON CONFLICT (pod_id) DO UPDATE SET floor_xid = EXCLUDED.floor_xid, "
                        "floor_seq = EXCLUDED.floor_seq, at = now() "
                        "WHERE (f.floor_xid, f.floor_seq) < "
                        "(EXCLUDED.floor_xid, EXCLUDED.floor_seq)) "
                        "SELECT count(*) FROM gone"
                    ),
                    params,
                )
            ).scalar_one()
        total += int(row)
        if int(row) < DELETE_CHUNK:
            return total


async def _live_subscribers(ctx: JobContext, pod_id: UUID) -> list[SubscriberRow]:
    """The pod's subscriber rows, minus those their module says are gone."""
    table = SubscriberTable(pod_id)
    rows = await table.all()
    live: list[SubscriberRow] = []
    for row in rows:
        found = durable_subscriber(row.subscriber)
        if found is None:
            # A prefix this process does not know is kept: dropping it would
            # let retention delete changes a newer process still owes.
            live.append(row)
            continue
        module, subscriber_id = found
        async with ctx.checkpoint() as uow:
            spec = await module.spec(uow, pod_id, subscriber_id)
        if spec is None:
            await table.delete(row.subscriber)
            continue
        live.append(row)
    return live


async def _behind(pod_id: UUID, subscriber: SubscriberRow) -> bool:
    page = await read_after(
        pod_id,
        encode_cursor(subscriber.cursor),
        table_ids=[subscriber.table_id],
        ops=sorted(subscriber.ops),
        limit=1,
    )
    return bool(page.changes)


async def _schemas_with_log(after: str) -> list[str]:
    async with get_datastore_engine().connect() as conn:
        if not await conn.scalar(
            text(f"SELECT to_regclass('{VERSIONS_TABLE}') IS NOT NULL")
        ):
            return []
        rows = await conn.execute(
            text(
                f"SELECT schema_name FROM {VERSIONS_TABLE} "
                "WHERE version >= :version AND schema_name > :after "
                "ORDER BY schema_name LIMIT :limit"
            ),
            {"version": CHANGE_LOG_VERSION, "after": after, "limit": RETENTION_PAGE},
        )
        return [str(name) for name in rows.scalars()]


def register_retention_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=RETENTION,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=RetentionPage,
        lease=timedelta(minutes=5),
    )(run_retention)
    registry.periodic(RETENTION, RETENTION_EVERY, RETENTION)


register_retention_work(default_registry)

__all__ = ["RETENTION", "RetentionPage", "register_retention_work", "retain"]
