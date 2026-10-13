"""The work runtime in a process: a dispatcher per enabled class, one listener, upkeep loops.

Started from the shared worker lifespan, so ``python -m app.worker``, the
lemma-cloud worker and the embedded worker in ``app/standalone.py`` all run it
with no entrypoint change. Shutdown stops claiming first, lets running work
finish or reach a safe point within the grace, then releases whatever this
process still owns -- charging nothing.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.work import maintenance
from app.core.infrastructure.work.config import (
    class_slots,
    enabled_classes,
    work_settings,
)
from app.core.infrastructure.work.dispatcher import Dispatcher
from app.core.infrastructure.work.execute import ExecEnv, SessionMaker
from app.core.infrastructure.work.gauges import gauge_loop
from app.core.infrastructure.work.limits import WorkLimitPort, current_work_limit_port
from app.core.infrastructure.work.listener import WorkWakeListener
from app.core.infrastructure.work.registry import (
    WorkRegistry,
    registry as default_registry,
)
from app.core.infrastructure.work.types import WorkClass
from app.core.log.log import get_logger
from app.core.request_context import create_background_task

logger = get_logger(__name__)


def process_owner() -> str:
    """A lease owner unique to this process incarnation, readable in pg_stat and the table."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


class WorkRuntime:
    def __init__(
        self,
        session_maker: SessionMaker,
        *,
        classes: list[WorkClass],
        registry: WorkRegistry | None = None,
        database_url: str | None = None,
        limits: WorkLimitPort | None = None,
        owner: str | None = None,
    ) -> None:
        self._session_maker = session_maker
        self._registry = registry or default_registry
        self._database_url = database_url
        self._limits = limits
        self.stopping = asyncio.Event()
        base = owner or process_owner()
        self.dispatchers = {
            cls: Dispatcher(
                cls,
                ExecEnv(
                    session_maker=session_maker,
                    registry=self._registry,
                    owner=f"{base}:{cls.value}",
                    statement_timeout_ms=work_settings.work_fast_statement_timeout_ms,
                    stopping=self.stopping,
                ),
                slots=class_slots(cls),
                quantum=work_settings.work_quantum,
                poll_interval=work_settings.work_poll_interval_seconds,
            )
            for cls in classes
        }
        self._tasks: list[asyncio.Task[None]] = []
        self._loops: list[asyncio.Task[None]] = []

    @property
    def classes(self) -> list[WorkClass]:
        return list(self.dispatchers)

    async def start(self) -> None:
        if not self.dispatchers:
            return
        async with self._session_maker() as session:
            armed = await maintenance.arm_periodics(
                session, self._registry, self.classes
            )
            await session.commit()
        for cls, dispatcher in self.dispatchers.items():
            self._tasks.append(
                create_background_task(
                    dispatcher.run(), name=f"work-dispatcher-{cls.value}"
                )
            )
        self._start_loops()
        logger.info(
            "work.runtime.started",
            classes=",".join(c.value for c in self.classes),
            periodics_armed=armed,
        )

    def _start_loops(self) -> None:
        if self._database_url and work_settings.work_listen_enabled:
            listener = WorkWakeListener(
                self._database_url,
                {cls: d.wake for cls, d in self.dispatchers.items()},
            )
            self._loops.append(
                create_background_task(listener.run(), name="work-wake-listener")
            )
        self._loops.append(
            create_background_task(
                gauge_loop(
                    self._session_maker, work_settings.work_gauge_interval_seconds
                ),
                name="work-gauges",
            )
        )
        self._loops.append(
            create_background_task(self._refresh_loop(), name="work-cap-refresh")
        )

    async def _refresh_loop(self) -> None:
        limits = self._limits or current_work_limit_port()
        while True:
            await asyncio.sleep(work_settings.work_cap_refresh_seconds)
            for cls in self.classes:
                try:
                    async with self._session_maker() as session:
                        await maintenance.refresh_queues(session, cls, limits)
                        await session.commit()
                except SQLAlchemyError, OSError:
                    logger.warning(
                        "work.queues.refresh_failed",
                        work_class=cls.value,
                        exc_info=True,
                    )

    async def stop(self, grace_seconds: float | None = None) -> None:
        if not self.dispatchers:
            return
        grace = (
            work_settings.work_shutdown_grace_seconds
            if grace_seconds is None
            else grace_seconds
        )
        self.stopping.set()
        for dispatcher in self.dispatchers.values():
            dispatcher.wake()
        if self._tasks:
            await asyncio.wait(self._tasks)
        released = await asyncio.gather(
            *(d.drain(grace) for d in self.dispatchers.values())
        )
        for task in self._loops:
            task.cancel()
        if self._loops:
            await asyncio.wait(self._loops)
        logger.info("work.runtime.stopped", released=sum(released))


@asynccontextmanager
async def work_runtime_lifespan(
    session_maker: SessionMaker,
    *,
    database_url: str | None,
    classes: list[WorkClass] | None = None,
    registry: WorkRegistry | None = None,
) -> AsyncIterator[WorkRuntime]:
    runtime = WorkRuntime(
        session_maker,
        classes=enabled_classes() if classes is None else classes,
        registry=registry,
        database_url=database_url,
    )
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()
