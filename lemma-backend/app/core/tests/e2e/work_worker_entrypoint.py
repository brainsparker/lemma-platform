"""``python -m app.worker`` plus the work kinds and one long streaq task the test needs.

Run as a module so it starts every lane exactly as production does.
"""

import asyncio

import app.core.tests.e2e.work_worker_handlers  # noqa: F401  registers the work kinds
import app.events  # noqa: F401  every module's tasks, as app.worker imports them
from app.core.infrastructure.jobs.streaq_runtime import run_worker_lanes, streaq_task


@streaq_task(name="e2e_work_hold_grace", timeout=120)
async def hold_grace() -> None:
    """Keeps streaq's shutdown grace busy, so the drain is seen to start before it ends."""
    await asyncio.sleep(90)


if __name__ == "__main__":
    asyncio.run(run_worker_lanes())
