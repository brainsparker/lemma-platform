"""How many function runs one pod may have executing at once, across replicas.

A pod's function sandbox has a fixed number of worker processes. Dispatching
past it used to fail the overflow inside the sandbox; the sandbox now queues it,
but by then the run is RUNNING and its execution budget is already being spent
on the wait. This gate holds the run back one step earlier, while it is still
PENDING, so a burst queues for free.

A sorted set per pod: each member is a run, scored by when its slot lapses on
its own. Releases are explicit, but a dispatcher that dies between acquiring
and releasing must not hold a slot forever, so every slot also expires --
shortly after the latest moment the run could still be executing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
import random
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger

logger = get_logger(__name__)

# KEYS[1] pod set; ARGV: now_ms, run_id, hold_until_ms, limit.
_ACQUIRE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
local held = redis.call('ZSCORE', KEYS[1], ARGV[2])
if (not held) and redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[4]) then
  return 0
end
redis.call('ZADD', KEYS[1], ARGV[3], ARGV[2])
local latest = redis.call('ZRANGE', KEYS[1], -1, -1, 'WITHSCORES')
redis.call('PEXPIREAT', KEYS[1], latest[2])
return 1
"""

# KEYS[1] pod set; ARGV: run_id, hold_until_ms. Extends only a slot still held.
_EXTEND_LUA = """
if not redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  return 0
end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
local latest = redis.call('ZRANGE', KEYS[1], -1, -1, 'WITHSCORES')
redis.call('PEXPIREAT', KEYS[1], latest[2])
return 1
"""

_FIRST_POLL_SECONDS = 0.2
_MAX_POLL_SECONDS = 2.0


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


class PodRunGate:
    """A per-pod counting semaphore in Redis, keyed by run id.

    Acquiring is re-entrant for the same run, so a redelivered dispatch does
    not take a second slot. Redis being unreachable opens the gate rather than
    closing it: the sandbox still queues for a free worker, so the cost of an
    open gate is budget spent waiting, while a closed one would stop every
    function on the platform.
    """

    def __init__(
        self,
        *,
        limit: int,
        redis_factory: Callable[[], Redis] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if limit < 1:
            raise ValueError("pod run limit must be positive")
        self._limit = limit
        # Resolved at call time, not bound as a default at import: a test that
        # patches the module's `get_redis` must reach this gate too.
        self._redis_factory = redis_factory or (lambda: get_redis())
        self._now = now

    @staticmethod
    def _key(pod_id: UUID) -> str:
        return f"function:pod-runs:{pod_id}"

    async def acquire(
        self,
        pod_id: UUID,
        run_id: UUID,
        *,
        hold_until: datetime,
        wait_until: datetime,
    ) -> bool:
        """Take a slot, waiting until ``wait_until`` for one to free.

        Returns False only when the wait ran out.
        """
        delay = _FIRST_POLL_SECONDS
        while True:
            try:
                acquired = await self._redis_factory().eval(
                    _ACQUIRE_LUA,
                    1,
                    self._key(pod_id),
                    _ms(self._now()),
                    str(run_id),
                    _ms(hold_until),
                    self._limit,
                )
            except RedisError:
                logger.warning(
                    "function.pod_run_gate.unavailable.degraded",
                    pod_id=str(pod_id),
                    exc_info=True,
                )
                return True
            if int(acquired) == 1:
                return True
            remaining = (wait_until - self._now()).total_seconds()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(remaining, delay * random.uniform(0.8, 1.2)))
            delay = min(delay * 2, _MAX_POLL_SECONDS)

    async def extend(self, pod_id: UUID, run_id: UUID, *, hold_until: datetime) -> None:
        try:
            await self._redis_factory().eval(
                _EXTEND_LUA,
                1,
                self._key(pod_id),
                str(run_id),
                _ms(hold_until),
            )
        except RedisError:
            logger.warning(
                "function.pod_run_gate.unavailable.degraded",
                pod_id=str(pod_id),
                exc_info=True,
            )

    async def release(self, pod_id: UUID, run_id: UUID) -> None:
        try:
            await self._redis_factory().zrem(self._key(pod_id), str(run_id))
        except RedisError:
            # The slot lapses by its own expiry; this only returns it sooner.
            logger.warning(
                "function.pod_run_gate.unavailable.degraded",
                pod_id=str(pod_id),
                exc_info=True,
            )
