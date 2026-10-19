"""Who in a pod has a live socket open: the ``presence`` call.

A sorted set per pod in Redis, ``live:presence:<pod>``, of user ids scored by
when their claim lapses. A socket claims its user on connect and again each
time it refreshes its slot (``limits.SLOT_REFRESH_SECONDS``); a user whose
last socket closes is removed, and one whose process died without saying so
lapses after ``limits.SLOT_TTL_SECONDS``. Redis being unreachable answers
"nobody", never an error: presence decorates a page, it gates nothing.
"""

from __future__ import annotations

import time
from uuid import UUID

from pydantic import JsonValue
from redis.exceptions import RedisError

from app.core.config import settings as core_settings
from app.core.infrastructure.redis.client import get_redis
from app.core.live.connection import LiveConnection
from app.core.live.limits import SLOT_TTL_SECONDS, slots_held
from app.core.live.protocol import JsonObject
from app.core.log.log import get_logger

logger = get_logger(__name__)

#: The most people one ``presence`` answer names.
MAX_PRESENT = 500


def _key(pod_id: UUID) -> str:
    return f"live:presence:{pod_id}"


async def mark_online(pod_id: UUID, user_id: UUID) -> None:
    key = _key(pod_id)
    redis = get_redis(url=core_settings.redis_url)
    try:
        await redis.zadd(key, {str(user_id): time.time() + SLOT_TTL_SECONDS})
        await redis.expire(key, int(SLOT_TTL_SECONDS) + 1)
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.presence_unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )


async def mark_offline(pod_id: UUID, user_id: UUID) -> None:
    """Forget the user's presence, unless another socket of theirs is open."""
    if await slots_held(pod_id, user_id) > 0:
        return
    redis = get_redis(url=core_settings.redis_url)
    try:
        await redis.zrem(_key(pod_id), str(user_id))
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.presence_unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )


async def online(pod_id: UUID) -> list[UUID]:
    redis = get_redis(url=core_settings.redis_url)
    try:
        members = await redis.zrangebyscore(
            _key(pod_id), time.time(), "+inf", start=0, num=MAX_PRESENT
        )
    except (RedisError, OSError) as exc:
        logger.warning(
            "live.presence_unavailable.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return []
    found: list[UUID] = []
    for member in members:
        raw = member.decode() if isinstance(member, bytes) else str(member)
        try:
            found.append(UUID(raw))
        except ValueError:
            continue
    return found


class PresenceCall:
    """``presence``: the pod's members with a live socket open, by user id."""

    name = "presence"

    def per_minute(self) -> int:
        return 30

    async def __call__(
        self, connection: LiveConnection, params: JsonObject, *, call_id: str
    ) -> JsonValue:
        return {"online": [str(user_id) for user_id in await online(connection.pod_id)]}


__all__ = ["PresenceCall", "mark_offline", "mark_online", "online"]
