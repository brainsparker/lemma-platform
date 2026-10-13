"""Jobs: external I/O under a lease kept alive by its own heartbeat.

The heartbeat is a separate coroutine, never part of the job's own loop. A
five-minute shell command inside an agent turn produces no events, so a
heartbeat tied to the job's progress would lapse mid-tool and let a second
worker resume the same turn. When a heartbeat finds the lease gone it cancels
the job before it can write anything else.
"""

from __future__ import annotations

import asyncio
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
    """

    def __init__(self, env: ExecEnv, spec: HandlerSpec, item: ClaimedItem) -> None:
        self._env = env
        self._spec = spec
        self.item = item
        self.completed = False
        self.lease_lost = False
        self._heartbeat: asyncio.Task[None] | None = None

    @property
    def stopping(self) -> bool:
        return self._env.stopping.is_set()

    async def until_stopping(self) -> None:
        """Return once the process starts shutting down -- for jobs that idle-wait."""
        await self._env.stopping.wait()

    def raise_if_stopping(self) -> None:
        if self.stopping:
            raise Released()

    def writer(self, uow: SqlAlchemyUnitOfWork) -> WorkWriter:
        return WorkWriter(uow.session, registry=self._env.registry)

    @asynccontextmanager
    async def checkpoint(self) -> AsyncIterator[SqlAlchemyUnitOfWork]:
        async with self._env.session_maker() as session:
            unit = SqlAlchemyUnitOfWork(session)
            yield unit
            await unit.commit()

    @asynccontextmanager
    async def complete_work(self) -> AsyncIterator[SqlAlchemyUnitOfWork]:
        # Stopped first: the completion holds the item's row lock, and a
        # heartbeat waiting on it would find the row gone and cancel a job that
        # has in fact finished.
        await self.stop_heartbeat()
        async with self._env.session_maker() as session:
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
        while True:
            await asyncio.sleep(lease.total_seconds() / 3)
            if not await self._extend(lease):
                self.lease_lost = True
                logger.warning(
                    "work.item.lease_lost",
                    item_id=str(self.item.id),
                    kind=self.item.kind,
                    ns=self.item.ns,
                )
                job.cancel()
                return

    async def _extend(self, lease: timedelta) -> bool:
        """``False`` only when the lease is provably gone; a DB blip is not proof."""
        try:
            async with self._env.session_maker() as session:
                extended = await extend_lease(
                    session, self.item.id, self._env.owner, lease
                )
                await session.commit()
        except SQLAlchemyError, OSError:
            logger.warning(
                "work.heartbeat.failed",
                item_id=str(self.item.id),
                kind=self.item.kind,
                exc_info=True,
            )
            return True
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
