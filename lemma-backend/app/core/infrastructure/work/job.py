"""Jobs: external I/O under a lease kept alive by its own heartbeat.

The heartbeat is a separate coroutine, never part of the job's own loop. A
five-minute shell command inside an agent turn produces no events, so a
heartbeat tied to the job's progress would lapse mid-tool and let a second
worker resume the same turn. When a heartbeat finds the lease gone it cancels
the job before it can write anything else.

A heartbeat that cannot reach the database cannot prove the lease is gone, but
it cannot prove it is held either: the lease runs out on the database's clock
whether or not anyone can say so. So the job keeps running only while the last
extension that did commit still covers it, with a margin, and is cancelled
before another worker could claim it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work.execute import (
    ExecEnv,
    capture,
    release_item,
    settle,
    validated_payload,
)
from app.core.infrastructure.work.lifecycle import extend_lease, finish_item, lock_owned
from app.core.infrastructure.work.registry import HandlerSpec
from app.core.infrastructure.work.types import ClaimedItem, LeaseLost, Released
from app.core.infrastructure.work.writer import WorkWriter
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task

logger = get_logger(__name__)

#: The share of a lease a job may run on after its last committed extension.
#: Below one so the job stops before the database's clock lets the lease go,
#: with room for the two clocks to disagree and for the reaper's next round.
_LEASE_TRUST = 0.8
#: Beats per lease while the database answers, and while it does not: a beat
#: that fails comes back sooner, so the job is stopped near the trust limit
#: rather than up to a third of a lease past it.
_BEATS_PER_LEASE = 3
_FAILING_BEATS_PER_LEASE = 10


class JobContext:
    """What a job handler is given.

    * ``checkpoint()`` -- a short, unfenced unit of work for intermediate state (a
      step row, a checkpoint). Committed on exit. Never hold it across I/O.
    * ``complete_work()`` -- the fenced completion transaction: write the job's
      outcome and follow-up work in it; the item is deleted in the same commit.
      Raises ``LeaseLost`` if another worker owns the item now. A job that
      returns without calling it is completed automatically.
    * ``stopping`` / ``raise_if_stopping()`` -- the process is shutting down;
      a job at a safe point raises ``Released`` and goes back to the queue
      with nothing charged.
    * ``lease_alive()`` -- whether the last committed extension still covers
      this run, asked of the local clock alone. For a job about to cause an
      effect the database cannot fence (an HTTP call, a message sent).
    """

    def __init__(self, env: ExecEnv, spec: HandlerSpec, item: ClaimedItem) -> None:
        self._env = env
        self._spec = spec
        self.item = item
        self.completed = False
        self.lease_lost = False
        self._heartbeat: asyncio.Task[None] | None = None
        # The claim that handed us the item set its lease a moment ago.
        self._extended_at = time.monotonic()

    @property
    def stopping(self) -> bool:
        return self._env.stopping.is_set()

    async def until_stopping(self) -> None:
        """Return once the process starts shutting down -- for jobs that idle-wait."""
        await self._env.stopping.wait()

    def lease_alive(self) -> bool:
        if self.lease_lost:
            return False
        trusted = self._spec.lease.total_seconds() * _LEASE_TRUST
        return time.monotonic() - self._extended_at < trusted

    def raise_if_stopping(self) -> None:
        if self.stopping:
            raise Released()

    def writer(self, uow: SqlAlchemyUnitOfWork) -> WorkWriter:
        return WorkWriter(uow.session, registry=self._env.registry)

    @asynccontextmanager
    async def checkpoint(self) -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with self._env.db.handler(self.item.cls) as session:
            unit = SqlAlchemyUnitOfWork(session)
            yield unit
            await unit.commit()

    @asynccontextmanager
    async def complete_work(self) -> AsyncIterator[SqlAlchemyUnitOfWork]:
        # Stopped first: the completion holds the item's row lock, and a
        # heartbeat waiting on it would find the row gone and cancel a job that
        # has in fact finished.
        await self.stop_heartbeat()
        async with self._env.db.handler(self.item.cls) as session:
            unit = SqlAlchemyUnitOfWork(session)
            if await lock_owned(session, self.item.id, self._env.owner) is None:
                raise LeaseLost(str(self.item.id))
            yield unit
            await finish_item(session, self.item, self._env.registry)
            await unit.commit()
        self.completed = True

    def start_heartbeat(self, job: asyncio.Task[Exception | None]) -> None:
        self._heartbeat = create_inherited_task(
            self._beat(job), name=f"work-heartbeat-{self.item.id}"
        )

    async def stop_heartbeat(self) -> None:
        task, self._heartbeat = self._heartbeat, None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.wait({task})

    async def _beat(self, job: asyncio.Task[Exception | None]) -> None:
        lease = self._spec.lease
        interval = lease.total_seconds() / _BEATS_PER_LEASE
        while True:
            await asyncio.sleep(interval)
            extended = await self._extend(lease)
            interval = lease.total_seconds() / (
                _BEATS_PER_LEASE if extended else _FAILING_BEATS_PER_LEASE
            )
            if extended is False or (extended is None and not self.lease_alive()):
                self.lease_lost = True
                logger.warning(
                    "work.item.lease_lost",
                    item_id=str(self.item.id),
                    kind=self.item.kind,
                    ns=self.item.ns,
                )
                job.cancel()
                return

    async def _extend(self, lease: timedelta) -> bool | None:
        """``True`` extended, ``False`` provably gone, ``None`` unknown (no answer)."""
        started = time.monotonic()
        try:
            # A database that hangs instead of refusing answers nothing too.
            async with asyncio.timeout(lease.total_seconds() / _BEATS_PER_LEASE):
                async with self._env.db.control() as session:
                    extended = await extend_lease(
                        session, self.item.id, self._env.owner, lease
                    )
                    await session.commit()
        except SQLAlchemyError, OSError, TimeoutError:
            logger.warning(
                "work.heartbeat.failed",
                item_id=str(self.item.id),
                kind=self.item.kind,
                exc_info=True,
            )
            return None
        if extended:
            # The new expiry counts from when the statement was sent, not from
            # when it came back: the database may have set it any time between.
            self._extended_at = started
        return extended


async def _finish_without_completion(ctx: JobContext) -> None:
    async with ctx.complete_work():
        pass


async def run_job(env: ExecEnv, spec: HandlerSpec, item: ClaimedItem) -> str:
    ctx = JobContext(env, spec, item)
    job = create_inherited_task(
        capture(_validated_job(spec, ctx, item)), name=f"work-job-{item.id}"
    )
    ctx.start_heartbeat(job)
    try:
        await asyncio.wait({job})
    finally:
        # Reached on our own cancellation too (shutdown past its grace): the
        # job goes with us, and the dispatcher releases what it owned.
        if not job.done():
            job.cancel()
            await asyncio.wait({job})
        await ctx.stop_heartbeat()
    if job.cancelled():
        return "lease_lost"
    error = job.result()
    if error is None and not ctx.completed:
        error = await capture(_finish_without_completion(ctx))
    return await _outcome(env, spec, item, error)


async def _outcome(
    env: ExecEnv, spec: HandlerSpec, item: ClaimedItem, error: Exception | None
) -> str:
    if error is None:
        return "done"
    if isinstance(error, LeaseLost):
        logger.warning(
            "work.item.lease_lost", item_id=str(item.id), kind=item.kind, ns=item.ns
        )
        return "lease_lost"
    if isinstance(error, Released):
        return await release_item(env, item)
    return await settle(env, spec, item, error)


async def _validated_job(spec: HandlerSpec, ctx: JobContext, item: ClaimedItem) -> None:
    payload = validated_payload(spec, item)
    await spec.handler(ctx, payload)
