"""Durable delivery from a pod's change log, as work items.

``changes.sync_subscribers``
    Written by a subscriber's module in the transaction that creates, edits,
    pauses or deletes it. Asks the module what the subscriber watches and
    upserts or deletes its row. Dedup covers only queued items, so an edit made
    while a sync runs produces a second sync, and the row cannot be left
    stale. It then asks for a delivery, now and once more after the longest a
    write can still be in flight: a write whose probe ran before the row
    existed carries no wake of its own.

``changes.deliver``
    One per (pod, subscriber), serialised by a concurrency key. Reads a page
    of up to 500 changes after the subscriber's cursor, fills in current rows
    for changes logged before the subscriber watched them, hands the page to
    the module in a main-database transaction that commits, and only then moves
    the cursor. A crash between the two repeats the page, and the module's
    keys (``chg:<xid>:<seq>``) absorb the repeat. A full page asks for itself
    again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import (
    JobContext,
    PermanentFailure,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    pod_namespace,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.datastore.changes.cursor import decode_cursor, encode_cursor
from app.modules.datastore.changes.reader import (
    Change,
    ChangeOp,
    fetch_rows,
    read_after,
)
from app.modules.datastore.changes.subscribers import (
    DeliverOutcome,
    SubscriberTable,
    durable_subscriber,
)
from app.modules.datastore.domain.write_intent import WriteIntent
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    CHANGE_LOG_VERSION,
    migrate_pod_schema,
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.repositories import DatastoreTableRepository
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.services.table_context import TableContext

logger = get_logger(__name__)

SYNC_SUBSCRIBERS = "changes.sync_subscribers"
DELIVER = "changes.deliver"
#: The second delivery a sync asks for: past the statement and
#: idle-in-transaction timeouts, so any write that was in flight while the
#: subscriber row appeared has committed or aborted by then.
CATCH_UP_AFTER = timedelta(minutes=2)


class SubscriberRef(BaseModel):
    pod_id: UUID
    subscriber: str


class SyncRequest(SubscriberRef):
    #: When the subscriber's module asked: a new subscriber starts with the
    #: first change written from then on, not from when the sync ran.
    since: datetime | None = None


def concurrency_key(pod_id: UUID, subscriber: str) -> str:
    return f"changes:{pod_id}:{subscriber}"


async def request_sync(work: WorkWriter, pod_id: UUID, subscriber: str) -> UUID | None:
    return await work.add(
        SYNC_SUBSCRIBERS,
        pod_namespace(pod_id),
        SyncRequest(
            pod_id=pod_id, subscriber=subscriber, since=datetime.now(timezone.utc)
        ),
        dedup_key=f"changes.sync:{pod_id}:{subscriber}",
        concurrency_key=concurrency_key(pod_id, subscriber),
    )


async def request_delivery(
    work: WorkWriter,
    pod_id: UUID,
    subscriber: str,
    *,
    delay: timedelta | None = None,
    late: bool = False,
) -> UUID | None:
    suffix = ":late" if late else ""
    return await work.add(
        DELIVER,
        pod_namespace(pod_id),
        SubscriberRef(pod_id=pod_id, subscriber=subscriber),
        dedup_key=f"changes.deliver:{pod_id}:{subscriber}{suffix}",
        concurrency_key=concurrency_key(pod_id, subscriber),
        delay=delay,
    )


def _resolve(subscriber: str):
    found = durable_subscriber(subscriber)
    if found is None:
        # Every module's subscriber registers at import, in every process that
        # runs work; a key nobody claims is a bug, not a transient.
        raise PermanentFailure(f"no durable subscriber registered for {subscriber!r}")
    return found


async def run_sync(ctx: JobContext, payload: SyncRequest) -> None:
    module, subscriber_id = _resolve(payload.subscriber)
    async with ctx.checkpoint() as uow:
        spec = await module.spec(uow, payload.pod_id, subscriber_id)
        table = (
            None
            if spec is None or not spec.ops
            else await DatastoreTableRepository(uow).get_by_datastore_and_name(
                payload.pod_id, spec.table_name
            )
        )
    rows = SubscriberTable(payload.pod_id)
    if spec is None or table is None:
        await rows.delete(payload.subscriber)
        return
    version = await migrate_pod_schema(
        get_datastore_engine(),
        schema_name_for_pod(payload.pod_id),
        target=CHANGE_LOG_VERSION,
        create=False,
    )
    if version < CHANGE_LOG_VERSION:
        return
    await rows.upsert(
        payload.subscriber,
        table_id=table.id,
        ops=spec.ops,
        delay=spec.delay,
        since=payload.since,
        snapshot_intents=spec.snapshot_intents,
    )
    async with ctx.complete_work() as uow:
        work = ctx.writer(uow)
        await request_delivery(work, payload.pod_id, payload.subscriber)
        await request_delivery(
            work, payload.pod_id, payload.subscriber, delay=CATCH_UP_AFTER, late=True
        )


async def run_deliver(ctx: JobContext, payload: SubscriberRef) -> None:
    rows = SubscriberTable(payload.pod_id)
    row = await rows.get(payload.subscriber)
    if row is None:
        return
    module, subscriber_id = _resolve(payload.subscriber)
    page = await read_after(
        payload.pod_id,
        encode_cursor(row.cursor),
        table_ids=[row.table_id],
        ops=sorted(row.ops),
    )
    if page.changes:
        async with ctx.checkpoint() as uow:
            changes = await _with_current_rows(
                uow, payload.pod_id, page.changes, row.snapshot_intents
            )
            outcome = await module.deliver(uow, payload.pod_id, subscriber_id, changes)
        if outcome is DeliverOutcome.GONE:
            await rows.delete(payload.subscriber)
            return
        if outcome is DeliverOutcome.HOLD:
            return
    # After the commit above, never before: a cursor that moved first would
    # skip the page if the commit then failed.
    await rows.advance(payload.subscriber, decode_cursor(page.cursor))
    if page.full:
        async with ctx.complete_work() as uow:
            await request_delivery(ctx.writer(uow), payload.pod_id, payload.subscriber)
        logger.debug(
            "datastore.changes.deliver.page_full.observed",
            subscriber=payload.subscriber,
        )


async def _with_current_rows(
    uow: SqlAlchemyUnitOfWork,
    pod_id: UUID,
    changes: Sequence[Change],
    snapshot_intents: frozenset[WriteIntent],
) -> list[Change]:
    """Fill the row of a change logged before anything watched its table.

    The subscriber watches changes committed after its row exists; a write
    already in flight when it was created was logged slim. What it gets for
    those is the row as it is now -- the best the log can say. A change whose
    intent the subscriber does not act on (a bulk write a trigger skips) was
    logged slim on purpose and stays that way.
    """

    def wants(c: Change) -> bool:
        return (
            c.new_row is None
            and c.op is not ChangeOp.DELETE
            and WriteIntent(c.intent) in snapshot_intents
        )

    missing = [c for c in changes if wants(c)]
    if not missing:
        return list(changes)
    entity = await DatastoreTableRepository(uow).get(missing[0].table_id)
    if entity is None:
        return list(changes)
    ctx = TableContext.from_table_entity(entity, schema_name_for_pod(pod_id))
    current = await fetch_rows(pod_id, ctx, [c.record_id for c in missing])
    return [
        replace(c, new_row=current.get(c.record_id, {})) if wants(c) else c
        for c in changes
    ]


def register_change_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SYNC_SUBSCRIBERS,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=SyncRequest,
        concurrency=1,
    )(run_sync)
    registry.handler(
        kind=DELIVER,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=SubscriberRef,
        concurrency=1,
    )(run_deliver)


register_change_work(default_registry)

__all__ = [
    "CATCH_UP_AFTER",
    "DELIVER",
    "SYNC_SUBSCRIBERS",
    "SubscriberRef",
    "register_change_work",
    "request_delivery",
    "request_sync",
]
