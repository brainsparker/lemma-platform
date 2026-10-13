"""Which notification a WhatsApp message carried, for the status that fails it.

Cloud API accepts a send with HTTP 200 and reports most failures later, in a
``statuses`` webhook naming the message id -- a recipient outside the 24-hour
window (131047), a number that cannot receive (131026). Without remembering what
the id was for, a notification that never arrived stayed recorded as delivered.

Only notifications are remembered: they are the outbound that has a durable row
to correct. Redis, with a TTL past any status Meta still sends about a message.
"""

from __future__ import annotations

from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger

logger = get_logger(__name__)

_SENT_MESSAGE_TTL_SECONDS = 7 * 24 * 60 * 60

_cache: RedisJsonCache | None = None


def _store() -> RedisJsonCache:
    global _cache
    if _cache is None or _cache._redis_url != settings.redis_url:
        _cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:whatsapp-sent",
            ttl_seconds=_SENT_MESSAGE_TTL_SECONDS,
        )
    return _cache


async def remember_sent(notification_id: object, message_id: str | None) -> None:
    """Record that ``message_id`` carried this notification. Best-effort."""
    if not notification_id or not message_id:
        return
    try:
        await _store().set_raw(message_id, str(notification_id))
    except RedisError, OSError:
        # The message went; only its later failure report loses its row.
        logger.warning(
            "agent_surfaces.whatsapp.sent_message_not_recorded.degraded",
            exc_info=True,
        )


async def notification_sent_as(message_id: str) -> str | None:
    """The notification a WhatsApp message carried, if it carried one."""
    return await _store().get_raw(message_id)
