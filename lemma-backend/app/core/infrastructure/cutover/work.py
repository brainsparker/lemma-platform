"""The cutover from the old event transport, as two work kinds, and its finish.

``cutover.legacy_sweep`` (admin, one item, queued once by migration
``0056_agent_surface_bundle_work``) runs from the first worker boot for as long
as the old transport's tables exist:

1. Every pass takes over what the old outbox owes (``takeover.py``): each owed
   delivery becomes one ``cutover.legacy_event`` item, and the old consumer's
   inbox row is settled so an old worker still running during the rolling
   deploy does not do it as well. After a full pass, later passes read only
   rows that occurred from a watermark on (``SweepState.since``). It does the
   same for the outbox of a separately configured datastore database
   (``register_legacy_outbox``).
2. It waits until neither table has been written for ``quiet_seconds`` --
   every process of the previous release has stopped: those processes wrote
   the outbox and the inbox continuously.
3. Then, once (``SweepState.converted``): it takes over everything still owed,
   watermark or not (a delivery whose old worker died holding its claim),
   converts the streaq jobs still in Redis whose work existed nowhere else, and
   queues the state routes (owed replies, open notifications, dispatched
   schedule runs).
4. With ``WORK_CUTOVER_AUTO_FINISH`` on (off by default; the single-process
   app, which has no rolling deploy, turns it on), it then runs ``finish``
   itself. Otherwise -- or
   while ``finish`` cannot take its lock -- it re-arms every ``rearm_seconds``
   while the old tables exist, still taking over whatever becomes owed, until
   an operator runs ``python -m app.cutover_finish``.

``finish`` drops the old transport, bounded: it takes over from the watermark
outside any lock until a pass takes fewer than ``SETTLE_BELOW``; then, in one
transaction with a ``lock_timeout``, locks both tables, takes over the last
rows and drops them, so nothing is written between the last take-over and the
drop. Before that lock it takes over the rest of a separate datastore
database's outbox under that outbox's own lock and drops it: its rows are
checked against the main inbox, which must still exist. A lock it cannot get in
time (an old process mid-transaction) is retried with backoff a bounded number
of times and reported, never waited on; while another outbox stays, so do the
main tables. Then it retires the legacy Redis groups and streams.

``cutover.legacy_event`` runs one owed delivery through the converter its
route registered (``routes.legacy_converter``). Each converter writes what the
new code writes for that event, keyed the same way, so a delivery that both
paths reached still takes effect once.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from pydantic_core import to_jsonable_python
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.cutover.legacy_streams import LEGACY_GROUPS, destroy_group
from app.core.infrastructure.cutover.routes import (
    STATE_ROUTES,
    STREAQ_ROUTES,
    retired_streaq_jobs,
    TAKEN_OVER_MARK,
    converter_for,
)
from app.core.infrastructure.cutover.other_outboxes import (
    LegacyOutbox,
    Sessions,
    drop_other_outboxes,
    lock_not_available,
    register_legacy_outbox,
    sweep_other_outboxes,
)
from app.core.infrastructure.cutover.streaq_jobs import forget_job, queued_jobs
from app.core.infrastructure.cutover.takeover import (
    BATCH,
    LEGACY_EVENT,
    LEGACY_NS,
    LEGACY_SWEEP,
    take_over,
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
from app.core.infrastructure.work.config import work_settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: How long both tables must go unwritten before the previous release is
#: taken to be gone: longer than a rolling deploy's slowest step.
QUIET_SECONDS = 600
POLL_SECONDS = 30
#: Once converted, how often the sweep looks again while the tables remain.
REARM_SECONDS = 300
SINCE_SLACK = timedelta(minutes=5)
#: ``finish`` takes the table lock only once a pass outside it takes fewer.
SETTLE_BELOW = 1_000
#: How long ``finish`` waits for the table lock before giving up the attempt.
LOCK_TIMEOUT_MS = 5_000


class LegacyEvent(BaseModel):
    route: str
    event: dict[str, Any] = Field(default_factory=dict)


class SweepState(BaseModel):
    quiet_seconds: int = QUIET_SECONDS
    poll_seconds: int = POLL_SECONDS
    rearm_seconds: int = REARM_SECONDS
    #: After a full pass, later passes read only rows that occurred from here.
    since: datetime | None = None
    #: Per other outbox: how far its rows have been read ([occurred_at, id]).
    cursors: dict[str, list[str]] = Field(default_factory=dict)
    #: The one-time conversion (streaq jobs, state routes) has run.
    converted: bool = False


# --- cutover.legacy_event ---------------------------------------------------


async def run_legacy_event(ctx: JobContext, payload: LegacyEvent) -> None:
    converter = converter_for(payload.route)
    if converter is None:
        raise PermanentFailure(f"no converter registered for {payload.route!r}")
    await converter(ctx, payload.event, payload.route)


# --- cutover.legacy_sweep ---------------------------------------------------


async def run_sweep(ctx: JobContext, state: SweepState) -> None:
    taken: dict[str, int] = {}
    last = None
    async with ctx.checkpoint() as uow:
        present = await legacy_tables_present(uow.session)
        if present:
            newest_row = await uow.session.scalar(
                text("SELECT max(occurred_at) FROM domain_event_outbox")
            )
            taken = await take_over(uow.session, limit=BATCH, since=state.since)
            last = await last_activity(uow.session)
            if newest_row is not None and not _full(taken):
                # Everything that occurred before this pass began is taken
                # over (no route filled its batch); a few minutes of slack for
                # rows committed after others that occurred later.
                state.since = newest_row - SINCE_SLACK
    other_taken, other_last, others_present = await sweep_other_outboxes(
        _job_sessions(ctx), state.cursors
    )
    busy = sum(taken.values()) + other_taken
    if busy:
        logger.info(
            "cutover.legacy_sweep.taken_over", routes=_counts(taken), other=other_taken
        )
    newest = max((t for t in (last, other_last) if t is not None), default=None)
    quiet = newest is None or datetime.now(UTC) - newest >= timedelta(
        seconds=state.quiet_seconds
    )
    # A full batch means more is waiting now.
    full = _full(taken) or other_taken >= BATCH
    if full or not quiet:
        await _rearm(ctx, state, 0 if full else state.poll_seconds)
        return
    if not state.converted:
        await convert(ctx)
        state.converted = True
    if not (present or others_present):
        return
    if work_settings.work_cutover_auto_finish:
        result = await finish(
            _job_sessions(ctx), since=state.since, cursors=state.cursors, attempts=3
        )
        if result.done:
            return
    # The tables stay until an operator's ``app.cutover_finish`` (or a later
    # pass that gets the lock) drops them.
    await _rearm(ctx, state, state.rearm_seconds)


def _job_sessions(ctx: JobContext) -> Sessions:
    @asynccontextmanager
    async def session() -> AsyncIterator[AsyncSession]:
        async with ctx.checkpoint() as uow:
            yield uow.session

    return session


async def _rearm(ctx: JobContext, state: SweepState, delay_seconds: int) -> None:
    async with ctx.complete_work() as uow:
        await WorkWriter.of(uow).add(
            LEGACY_SWEEP,
            SYSTEM_NAMESPACE,
            state,
            dedup_key=LEGACY_SWEEP,
            delay=timedelta(seconds=delay_seconds),
        )


def _full(taken: dict[str, int]) -> bool:
    """Some route filled its batch: more is waiting right now."""
    return any(count >= BATCH for count in taken.values())


async def convert(ctx: JobContext) -> int:
    """The previous release is gone: take over what is still owed, watermark or
    not, convert what only Redis held and queue the state routes. Once."""
    while True:
        async with ctx.checkpoint() as uow:
            if not await legacy_tables_present(uow.session):
                break
            if not _full(await take_over(uow.session, limit=BATCH)):
                break
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
    logger.info("cutover.legacy_sweep.converted", streaq_converted=converted)
    return converted


# --- finish: drop the old transport -------------------------------------------


@dataclass(frozen=True, slots=True)
class FinishResult:
    #: ``domain_event_outbox`` and ``domain_event_inbox`` are gone.
    dropped: bool
    #: Attempts that gave up on the table lock.
    lock_timeouts: int
    #: Deliveries taken over on the way.
    taken: int
    #: Other databases' outboxes still there (their lock was not granted).
    outboxes_left: tuple[str, ...] = ()

    @property
    def done(self) -> bool:
        return self.dropped and not self.outboxes_left


async def finish(
    sessions: Sessions,
    *,
    since: datetime | None,
    cursors: dict[str, list[str]] | None = None,
    lock_timeout_ms: int = LOCK_TIMEOUT_MS,
    attempts: int = 5,
    backoff_seconds: float = 2.0,
) -> FinishResult:
    """Take over the last of what is owed and drop the old transport, bounded.

    Never waits on a lock longer than ``lock_timeout_ms``: an attempt that
    cannot get it rolls back, backs off and tries again, ``attempts`` times in
    all, and the result says so. Safe to run again at any point.
    """
    taken, since = await _settle(sessions, since)
    # Other databases' outboxes first: their rows are taken over against the
    # main inbox, so each is taken over to the end and dropped while that
    # inbox still exists. One whose lock is not granted keeps the main tables
    # too, for the next attempt to take it over against.
    other_taken, left = await drop_other_outboxes(
        sessions, dict(cursors or {}), lock_timeout_ms
    )
    taken += other_taken
    lock_timeouts, dropped = 0, False
    if not left:
        main_taken, dropped, lock_timeouts = await _drop_main(
            sessions, since, lock_timeout_ms, attempts, backoff_seconds
        )
        taken += main_taken
    if dropped:
        await retire_legacy_streams()
    result = FinishResult(
        dropped=dropped, lock_timeouts=lock_timeouts, taken=taken, outboxes_left=left
    )
    logger.info(
        "cutover.finish.finished",
        dropped_tables=dropped,
        lock_timeouts=lock_timeouts,
        taken=taken,
        outboxes_left=",".join(left),
    )
    return result


async def _drop_main(
    sessions: Sessions,
    since: datetime | None,
    lock_timeout_ms: int,
    attempts: int,
    backoff_seconds: float,
) -> tuple[int, bool, int]:
    """``_drop_locked``, retried with backoff: (taken, dropped, lock timeouts)."""
    taken = 0
    for attempt in range(1, attempts + 1):
        try:
            taken += await _drop_locked(sessions, since, lock_timeout_ms)
        except DBAPIError as exc:
            if not lock_not_available(exc):
                raise
            logger.warning(
                "cutover.finish.lock_timeout", attempt=attempt, attempts=attempts
            )
            if attempt < attempts:
                await asyncio.sleep(backoff_seconds * 2 ** (attempt - 1))
            continue
        return taken, True, attempt - 1
    return taken, False, attempts


async def _settle(
    sessions: Sessions, since: datetime | None
) -> tuple[int, datetime | None]:
    """Take over outside any lock until a pass takes fewer than ``SETTLE_BELOW``,
    advancing the watermark as the sweep does; (taken, watermark)."""
    total = 0
    while True:
        async with sessions() as session:
            if not await legacy_tables_present(session):
                return total, since
            newest_row = await session.scalar(
                text("SELECT max(occurred_at) FROM domain_event_outbox")
            )
            taken = await take_over(session, limit=BATCH, since=since)
            await session.commit()
        total += sum(taken.values())
        if newest_row is not None and not _full(taken):
            since = newest_row - SINCE_SLACK
        if sum(taken.values()) < SETTLE_BELOW:
            return total, since


async def _drop_locked(
    sessions: Sessions, since: datetime | None, lock_timeout_ms: int
) -> int:
    """One transaction: lock both tables, take over the last rows, drop them."""
    taken = 0
    async with sessions() as session:
        if not await legacy_tables_present(session):
            return 0
        await session.execute(
            text(f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms'")
        )
        await session.execute(
            text(
                "LOCK TABLE domain_event_outbox, domain_event_inbox "
                "IN ACCESS EXCLUSIVE MODE"
            )
        )
        while True:
            batch = await take_over(session, limit=BATCH, since=since)
            taken += sum(batch.values())
            if not _full(batch):
                break
        await session.execute(text("DROP TABLE domain_event_inbox"))
        await session.execute(text("DROP TABLE domain_event_outbox"))
        await session.commit()
    return taken


def _counts(by_name: dict[str, int]) -> str:
    """``name=count`` pairs as one string: a dict field below error is dropped."""
    return ",".join(f"{name}={n}" for name, n in sorted(by_name.items()) if n)


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


async def convert_streaq_jobs(ctx: JobContext) -> int:
    """Each streaq job only Redis held becomes a ``cutover.legacy_event``."""
    from app.core.infrastructure.redis.client import get_redis

    redis = get_redis(decode_responses=False)
    converted, left = 0, {}
    retired = retired_streaq_jobs()
    try:
        async for job in queued_jobs(redis):
            if job.function in STREAQ_ROUTES:
                # One job's arguments, as JSON (a UUID or a datetime as text).
                event = to_jsonable_python(
                    {"args": list(job.args), "kwargs": job.kwargs}, fallback=str
                )
                async with ctx.checkpoint() as uow:
                    await WorkWriter.of(uow).add(
                        LEGACY_EVENT,
                        LEGACY_NS,
                        LegacyEvent(route=f"streaq:{job.function}", event=event),
                    )
                converted += 1
            elif job.function not in retired:
                left[job.function] = left.get(job.function, 0) + 1
                continue
            await forget_job(redis, job)
    except (RedisError, OSError) as exc:
        raise Unavailable(
            "redis unreachable while converting streaq jobs",
            evidence=UnavailableEvidence.CONNECTION_REFUSED,
        ) from exc
    if left:
        logger.warning(
            "cutover.legacy_sweep.streaq_unconverted", functions=_counts(left)
        )
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
        logger.warning("cutover.finish.streams_not_retired", exc_info=True)


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
    "FinishResult",
    "LegacyEvent",
    "LegacyOutbox",
    "SweepState",
    "TAKEN_OVER_MARK",
    "convert",
    "finish",
    "last_activity",
    "legacy_tables_present",
    "register_cutover_work",
    "register_legacy_outbox",
    "retire_legacy_streams",
    "run_legacy_event",
    "run_sweep",
]
