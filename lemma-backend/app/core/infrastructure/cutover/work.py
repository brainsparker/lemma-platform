"""The cutover from the old event transport, as two work kinds. Worker only.

``cutover.legacy_sweep`` (admin, one item, queued once by migration
``0063_drop_event_outbox``) runs from the first worker boot until the old
transport is gone:

1. Every pass takes over what the old outbox owes (``takeover.py``): each owed
   delivery becomes one ``cutover.legacy_event`` item, and the old consumer's
   inbox row is settled so an old worker still running during the rolling
   deploy does not do it as well. It does the same for the outbox of a
   separately configured datastore database (``register_legacy_outbox``).
2. It waits until neither table has been written for ``quiet_seconds`` --
   every process of the previous release has stopped: those processes wrote
   the outbox and the inbox continuously.
3. Then, once: it converts the streaq jobs still in Redis whose work existed
   nowhere else, queues the state routes (owed replies, open notifications,
   dispatched schedule runs), takes over the last rows and drops
   ``domain_event_outbox`` and ``domain_event_inbox`` in one transaction --
   locked, so nothing is written between the last take-over and the drop --
   drops the datastore database's outbox, and retires the legacy Redis groups
   and streams.

The tables are dropped here and not by the migration because the migration
runs before the new release rolls out: the previous release's processes keep
writing both tables until they stop, and dropping a table under them would
fail every transaction they commit. A fresh install, which has nothing to wait
for, is dropped by the migration itself.

``cutover.legacy_event`` runs one owed delivery through the converter its
route registered (``routes.legacy_converter``). Each converter writes what the
new code writes for that event, keyed the same way, so a delivery that both
paths reached still takes effect once.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.infrastructure.cutover.legacy_streams import LEGACY_GROUPS, destroy_group
from app.core.infrastructure.cutover.routes import (
    ROUTES,
    STATE_ROUTES,
    STREAQ_ROUTES,
    STREAQ_SEEDED,
    TAKEN_OVER_MARK,
    converter_for,
)
from app.core.infrastructure.cutover.streaq_jobs import forget_job, queued_jobs
from app.core.infrastructure.cutover.takeover import (
    LEGACY_EVENT,
    LEGACY_NS,
    LEGACY_SWEEP,
    take_over,
    take_over_rows,
)
from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    Exponential,
    JobContext,
    PermanentFailure,
    RetryPolicy,
    Unavailable,
    UnavailableEvidence,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    registry as default_registry,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: Rows per route per pass.
BATCH = 5_000
#: How long both tables must go unwritten before the previous release is
#: taken to be gone: longer than a rolling deploy's slowest step.
QUIET_SECONDS = 600
POLL_SECONDS = 30
_TABLES = ("domain_event_outbox", "domain_event_inbox")


class LegacyEvent(BaseModel):
    route: str
    event: dict[str, Any] = Field(default_factory=dict)


class SweepState(BaseModel):
    quiet_seconds: int = QUIET_SECONDS
    poll_seconds: int = POLL_SECONDS
    #: Per other outbox: how far its rows have been read ([occurred_at, id]).
    cursors: dict[str, list[str]] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LegacyOutbox:
    """Another database's ``domain_event_outbox``, and the routes it feeds."""

    name: str
    #: The database's engine, or ``None`` when it is the main database (whose
    #: outbox the main take-over already reads).
    engine: Callable[[], AsyncEngine | None]
    routes: tuple[str, ...]
    like: str


_other_outboxes: dict[str, LegacyOutbox] = {}


def register_legacy_outbox(outbox: LegacyOutbox) -> None:
    """A module whose events had an outbox of their own (the datastore database)."""
    _other_outboxes[outbox.name] = outbox


# --- cutover.legacy_event ---------------------------------------------------


async def run_legacy_event(ctx: JobContext, payload: LegacyEvent) -> None:
    converter = converter_for(payload.route)
    if converter is None:
        raise PermanentFailure(f"no converter registered for {payload.route!r}")
    await converter(ctx, payload.event, payload.route)


# --- cutover.legacy_sweep ---------------------------------------------------


async def run_sweep(ctx: JobContext, state: SweepState) -> None:
    async with ctx.checkpoint() as uow:
        present = await legacy_tables_present(uow.session)
        taken = await take_over(uow.session, limit=BATCH) if present else {}
        last = await last_activity(uow.session) if present else None
    other_taken, other_last = await _sweep_other_outboxes(ctx, state, final=False)
    busy = sum(taken.values()) + other_taken
    if busy:
        logger.info("cutover.legacy_sweep.taken_over", routes=taken, other=other_taken)
    newest = max((t for t in (last, other_last) if t is not None), default=None)
    quiet = newest is None or datetime.now(UTC) - newest >= timedelta(
        seconds=state.quiet_seconds
    )
    # A full batch means more is waiting now; otherwise, once quiet, the last
    # take-over happens under the lock in ``finish``.
    if busy >= BATCH or not quiet:
        async with ctx.complete_work() as uow:
            await WorkWriter.of(uow).add(
                LEGACY_SWEEP,
                SYSTEM_NAMESPACE,
                state,
                dedup_key=LEGACY_SWEEP,
                delay=timedelta(seconds=0 if busy >= BATCH else state.poll_seconds),
            )
        return
    await finish(ctx, state)


async def finish(ctx: JobContext, state: SweepState) -> None:
    """The previous release is gone: convert what only Redis held, drop the rest."""
    converted = await convert_streaq_jobs(ctx)
    async with ctx.checkpoint() as uow:
        work = WorkWriter.of(uow)
        for route in STATE_ROUTES:
            await work.add(
                LEGACY_EVENT,
                LEGACY_NS,
                LegacyEvent(route=route),
                dedup_key=f"legacy:{route}",
            )
    dropped = False
    async with ctx.checkpoint() as uow:
        if await legacy_tables_present(uow.session):
            await uow.session.execute(
                text(
                    "LOCK TABLE domain_event_outbox, domain_event_inbox "
                    "IN ACCESS EXCLUSIVE MODE"
                )
            )
            while sum((await take_over(uow.session, limit=50_000)).values()):
                pass
            await uow.session.execute(text("DROP TABLE domain_event_inbox"))
            await uow.session.execute(text("DROP TABLE domain_event_outbox"))
            dropped = True
    await _sweep_other_outboxes(ctx, state, final=True)
    await retire_legacy_streams()
    logger.info(
        "cutover.legacy_sweep.finished",
        streaq_converted=converted,
        dropped_tables=dropped,
    )


async def legacy_tables_present(session: AsyncSession) -> bool:
    return bool(
        await session.scalar(
            text(
                "SELECT to_regclass('domain_event_outbox') IS NOT NULL "
                "AND to_regclass('domain_event_inbox') IS NOT NULL"
            )
        )
    )


async def last_activity(session: AsyncSession) -> datetime | None:
    """When the previous release last wrote either table.

    The inbox is read per status through its ``(status, last_received_at)``
    index; rows this cutover took over carry their event's own time, so the
    sweep's own writes never look like activity.
    """
    return await session.scalar(
        text(
            "SELECT GREATEST("
            "(SELECT max(occurred_at) FROM domain_event_outbox), "
            "(SELECT max(m) FROM (SELECT (SELECT max(last_received_at) "
            "FROM domain_event_inbox WHERE status = s) AS m FROM unnest(ARRAY["
            "'PROCESSING', 'RETRYING', 'COMPLETED', 'TERMINAL', 'DEAD_LETTER']) "
            "AS s) per_status))"
        )
    )


async def _sweep_other_outboxes(
    ctx: JobContext, state: SweepState, *, final: bool
) -> tuple[int, datetime | None]:
    taken, newest = 0, None
    routes = {route.consumer: route for route in ROUTES}
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
        newest = last if newest is None or (last and last > newest) else newest
        while True:
            rows = await _read_after(engine, outbox, state.cursors.get(outbox.name))
            if not rows:
                break
            async with ctx.checkpoint() as uow:
                for name in outbox.routes:
                    taken += await take_over_rows(uow.session, routes[name], rows)
            last_row = rows[-1]
            state.cursors[outbox.name] = [
                last_row["occurred_at"].isoformat(),
                str(last_row["id"]),
            ]
            if not final and taken >= BATCH:
                break
        if final:
            async with engine.begin() as conn:
                await conn.execute(text("DROP TABLE IF EXISTS domain_event_outbox"))
            logger.info("cutover.legacy_sweep.dropped_outbox", database=outbox.name)
    return taken, newest


async def _read_after(
    engine: AsyncEngine, outbox: LegacyOutbox, cursor: list[str] | None
) -> list[dict[str, Any]]:
    after_at, after_id = (
        (datetime.fromisoformat(cursor[0]), UUID(cursor[1]))
        if cursor
        else (datetime(1970, 1, 1, tzinfo=UTC), UUID(int=0))
    )
    async with engine.connect() as conn:
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


async def convert_streaq_jobs(ctx: JobContext) -> int:
    """Each streaq job only Redis held becomes a ``cutover.legacy_event``."""
    from app.core.infrastructure.redis.client import get_redis

    redis = get_redis(decode_responses=False)
    converted, left = 0, {}
    try:
        async for job in queued_jobs(redis):
            if job.function in STREAQ_ROUTES:
                event = json.loads(
                    json.dumps(
                        {"args": list(job.args), "kwargs": job.kwargs}, default=str
                    )
                )
                async with ctx.checkpoint() as uow:
                    await WorkWriter.of(uow).add(
                        LEGACY_EVENT,
                        LEGACY_NS,
                        LegacyEvent(route=f"streaq:{job.function}", event=event),
                    )
                converted += 1
            elif job.function not in STREAQ_SEEDED:
                left[job.function] = left.get(job.function, 0) + 1
                continue
            await forget_job(redis, job)
    except (RedisError, OSError) as exc:
        raise Unavailable(
            "redis unreachable while converting streaq jobs",
            evidence=UnavailableEvidence.CONNECTION_REFUSED,
        ) from exc
    if left:
        logger.warning("cutover.legacy_sweep.streaq_unconverted", functions=left)
    return converted


async def retire_legacy_streams() -> None:
    """Destroy the old consumer groups and their streams. Best effort: nothing
    reads them, and a Redis that cannot be reached keeps them harmlessly."""
    from app.core.infrastructure.redis.client import get_redis

    redis = get_redis(decode_responses=False)
    try:
        for stream, group in LEGACY_GROUPS:
            await destroy_group(redis, stream, group)
        await redis.delete(*sorted({stream for stream, _ in LEGACY_GROUPS}))
    except RedisError, OSError:
        logger.warning("cutover.legacy_sweep.streams_not_retired", exc_info=True)


def register_cutover_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=LEGACY_EVENT,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=LegacyEvent,
        lease=timedelta(minutes=2),
        retry=RetryPolicy(
            max_attempts=5,
            max_lease_losses=3,
            unavailable_budget=timedelta(hours=24),
            backoff=Exponential(
                base=timedelta(seconds=10), cap=timedelta(minutes=10), jitter=0.2
            ),
        ),
    )(run_legacy_event)
    registry.handler(
        kind=LEGACY_SWEEP,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=SweepState,
        lease=timedelta(minutes=5),
        retry=RetryPolicy(
            max_attempts=20,
            max_lease_losses=10,
            unavailable_budget=timedelta(days=7),
            backoff=Exponential(
                base=timedelta(seconds=30), cap=timedelta(minutes=10), jitter=0.2
            ),
        ),
    )(run_sweep)


register_cutover_work(default_registry)

__all__ = [
    "LegacyEvent",
    "LegacyOutbox",
    "SweepState",
    "TAKEN_OVER_MARK",
    "finish",
    "last_activity",
    "legacy_tables_present",
    "register_cutover_work",
    "register_legacy_outbox",
    "run_legacy_event",
    "run_sweep",
]
