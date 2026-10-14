"""Recurring maintenance as periodic work items: what a ``@streaq_cron`` became.

``@periodic_job(kind, every=..., cls=...)`` registers a zero-argument coroutine
as a JOB kind and arms it as a periodic named after the kind: one item in the
``system`` namespace, armed at worker boot, re-added ``every`` after each run in
that run's completion transaction. So exactly one chain exists however many
workers there are, and a missed tick is impossible rather than recovered.

A cron tick that failed simply waited for the next tick, and a periodic keeps
that contract: a body that raises is logged at ERROR with its traceback and the
run completes, so the chain re-arms. Left to the runtime's retry policy instead,
three bad ticks would send the periodic ``dead`` and stop it until the next
deploy -- a database blip turning into a sweep that silently never runs again.
Shutdown (``Released``) and cancellation still propagate.

There is no wall-clock alignment: a daily sweep runs 24 h after the previous
run (first run at the first worker boot that arms it), not at 04:00. For
retention and reconciliation that is fine; nothing here is a calendar.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

from pydantic import BaseModel

from app.core.infrastructure.work.job import JobContext
from app.core.infrastructure.work.registry import WorkRegistry, registry
from app.core.infrastructure.work.types import Released, WorkClass, WorkMode
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: What a streaq job got (``JOB_TIMEOUT_SECONDS``): a sweep is budgeted well
#: inside it, and one that is not is cancelled rather than left holding a slot.
DEFAULT_PERIODIC_MAX_RUNTIME = timedelta(minutes=30)

type PeriodicBody = Callable[[], Awaitable[object]]


class NoPayload(BaseModel):
    """A periodic carries nothing: it always means "do the sweep now"."""


def periodic_job(
    kind: str,
    *,
    every: timedelta,
    cls: WorkClass = WorkClass.ADMIN,
    max_runtime: timedelta = DEFAULT_PERIODIC_MAX_RUNTIME,
    work_registry: WorkRegistry | None = None,
) -> Callable[[PeriodicBody], PeriodicBody]:
    """Run the decorated coroutine every ``every``, as kind (and periodic) ``kind``.

    ``cls`` is ``ADMIN`` for retention and housekeeping, ``AUTOMATION`` for a
    check that talks to the outside world. Returns the body unchanged, so a
    test calls it directly as before.
    """
    target = work_registry or registry

    def register(body: PeriodicBody) -> PeriodicBody:
        async def run(ctx: JobContext, payload: NoPayload) -> None:
            del ctx, payload
            try:
                await body()
            except asyncio.CancelledError, Released:
                raise
            except Exception:  # noqa: BLE001 - a failed tick waits for the next
                logger.error("work.periodic.failed", kind=kind, exc_info=True)

        run.__name__ = run.__qualname__ = f"periodic_{body.__name__}"
        target.handler(
            kind=kind,
            cls=cls,
            mode=WorkMode.JOB,
            payload=NoPayload,
            max_runtime=max_runtime,
        )(run)
        target.periodic(kind, every, kind)
        return body

    return register


__all__ = ["DEFAULT_PERIODIC_MAX_RUNTIME", "NoPayload", "periodic_job"]
