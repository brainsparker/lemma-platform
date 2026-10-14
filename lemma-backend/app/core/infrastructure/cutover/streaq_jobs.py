"""Jobs the old streaq queues still hold, read without streaq.

streaq kept each queued job's data under ``streaq:<queue>:task:data:<id>`` as a
pickled ``{"f": <function>, "a": args, "k": kwargs, ...}`` written by this
application. The cutover sweep converts the jobs whose work existed only here
and deletes them; the cutover check counts every job left, by function.
"""

from __future__ import annotations

import pickle  # noqa: S403 - data this application wrote to its own Redis
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis

_DATA_PATTERN = b"streaq:*:task:data:*"


@dataclass(frozen=True, slots=True)
class QueuedJob:
    key: bytes
    function: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


async def queued_jobs(redis: Redis) -> AsyncIterator[QueuedJob]:
    """Every job whose data is still in Redis; ``redis`` must not decode."""
    async for key in redis.scan_iter(match=_DATA_PATTERN, count=500):
        raw = await redis.get(key)
        if not isinstance(raw, bytes):
            continue
        try:
            data = pickle.loads(raw)  # noqa: S301 - see the module docstring
        except pickle.UnpicklingError, EOFError, AttributeError, ImportError:
            yield QueuedJob(key=key, function="<unreadable>")
            continue
        if not isinstance(data, dict):
            continue
        yield QueuedJob(
            key=key,
            function=str(data.get("f") or "<unknown>"),
            args=tuple(data.get("a") or ()),
            kwargs=dict(data.get("k") or {}),
        )


async def forget_job(redis: Redis, job: QueuedJob) -> None:
    """Delete a job's data once it is converted (or its work seeded elsewhere)."""
    await redis.delete(job.key)


async def jobs_by_function(redis: Redis) -> dict[str, int]:
    counts: dict[str, int] = {}
    async for job in queued_jobs(redis):
        counts[job.function] = counts.get(job.function, 0) + 1
    return counts
