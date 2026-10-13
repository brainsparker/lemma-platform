"""Where the work runtime meets the streaq worker it runs beside.

The worker process still hosts streaq's lanes; the work runtime starts inside
the primary lane's lifespan and must begin draining the moment that lane gets
SIGTERM. Both hooks live here so the streaq runtime only names them.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    from streaq import Worker

    from app.core.infrastructure.work.runtime import WorkRuntime


@dataclass(slots=True)
class _WorkRuntimeSlot:
    """The process's work runtime, once the primary lifespan has started it."""

    runtime: WorkRuntime | None = None


_work_runtime = _WorkRuntimeSlot()


def drain_work_on_signal[C](worker: Worker[C]) -> None:
    """Start draining the work runtime the moment the primary lane gets SIGTERM.

    streaq's handler cancels the lane's scope and then gives its in-flight
    tasks up to ``grace_period`` before the lifespan unwinds -- and the work
    runtime is stopped by that unwinding, so its own drain used to start only
    after streaq's grace had run out, leaving its jobs to the platform's
    SIGKILL. Wrapping the handler starts it as the signal arrives instead:
    claiming stops, jobs reach a safe point and are released with nothing
    charged, in parallel with streaq's grace rather than after it.

    The wrapped handler returns without awaiting anything once it has
    cancelled the scope, so the hook below runs before the cancellation is
    delivered; ``begin_stop`` is synchronous for that reason.
    """
    original = worker.signal_handler

    async def _signal_handler(scope: object) -> None:
        await original(scope)  # type: ignore[arg-type]
        runtime = _work_runtime.runtime
        if runtime is not None:
            runtime.begin_stop()

    worker.signal_handler = _signal_handler  # type: ignore[method-assign]


async def enter_work_runtime(stack: AsyncExitStack) -> None:
    """Start the work runtime on ``stack``, after the module lifespans.

    Entered after them so it unwinds before them: handlers may use what those
    set up, and shutdown must stop claiming and release owned work while the
    engine is still open. It gets its own pool, sized to what its slots may
    hold, so a busy streaq lane can never make a work handler wait for a
    connection.
    """
    from app.core.infrastructure.work.runtime import work_runtime_lifespan

    _work_runtime.runtime = await stack.enter_async_context(
        work_runtime_lifespan(database_url=settings.database_url)
    )


def forget_work_runtime() -> None:
    """The lifespan is unwinding: a late signal has nothing left to drain."""
    _work_runtime.runtime = None
