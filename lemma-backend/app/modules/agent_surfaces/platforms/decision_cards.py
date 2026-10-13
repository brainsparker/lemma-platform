"""Where an approval card was posted, so it can be retired later.

A card outlives the pause it asks about. When the person types something else
instead of tapping, the new turn denies the pause on their behalf -- and the
card used to stay on screen with live Approve and Deny buttons, inviting a tap
that could only be answered "already cancelled". Remembering where each card
went is what lets the platform take its buttons away when that happens.

Keyed by the card's callback id (``conversation_id|tool_call_id``), which every
adapter already holds while rendering. Best-effort in both directions: a card
whose handle could not be stored or read keeps its buttons, and a tap on it is
still answered correctly by the interaction path. Held for a month: a pause
waits as long as the person takes, and a handle that outlives its card costs a
few hundred bytes.
"""

from __future__ import annotations

from pydantic import ValidationError
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.decision_card import DecisionCardHandle

logger = get_logger(__name__)

_CARD_TTL_SECONDS = 30 * 24 * 60 * 60

_STORE_ERRORS = (RedisError, OSError)

_store: RedisJsonCache | None = None


def _cards() -> RedisJsonCache:
    global _store
    if _store is None or _store._redis_url != settings.redis_url:
        _store = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:decision-card",
            ttl_seconds=_CARD_TTL_SECONDS,
        )
    return _store


async def remember_decision_card(callback_id: str, handle: DecisionCardHandle) -> None:
    """Record where the card for ``callback_id`` was posted. Never raises."""
    if not callback_id:
        return
    try:
        await _cards().set_json(callback_id, handle.model_dump(mode="json"))
    except _STORE_ERRORS:
        logger.warning(
            "agent_surfaces.decision_cards.handle_not_stored.degraded",
            platform=handle.platform,
            exc_info=True,
        )


async def take_decision_card(callback_id: str) -> DecisionCardHandle | None:
    """Where the card for ``callback_id`` is, forgetting it. Never raises."""
    try:
        stored = await _cards().get_json(callback_id)
        if stored is not None:
            await _cards().delete(callback_id)
    except _STORE_ERRORS:
        logger.warning(
            "agent_surfaces.decision_cards.handle_not_read.degraded", exc_info=True
        )
        return None
    except ValueError:
        return None
    try:
        return DecisionCardHandle.model_validate(stored) if stored else None
    except ValidationError:
        return None


__all__ = ["DecisionCardHandle", "remember_decision_card", "take_decision_card"]
