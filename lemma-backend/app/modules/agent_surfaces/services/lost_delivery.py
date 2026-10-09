"""Telling a person once when their message failed before it reached a run.

An inbound delivery is prepared under the event inbox, which retries a failure
a bounded number of times and then dead-letters it -- and dead-lettering is a
log line. The person saw their message go and heard nothing back, which they
cannot tell from being ignored.

So when the inbox's last try fails, or a failure the inbox will not retry at
all, the sender of a direct message is told, once, to send it again. A group is
left alone: a reply there is read by everyone in it, about one person's message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.delivery_limits import CONSUMER_ATTEMPTS
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.services.turn_starter import (
    PROCESSING_FAILED_REPLY,
    SurfaceTurnStarter,
)

logger = get_logger(__name__)

#: Long enough to span every inbox retry of one delivery.
_FAILURE_COUNT_TTL_SECONDS = 60 * 60


def inbox_will_not_retry(exc: BaseException) -> bool:
    """The failures the inbox records as terminal on their first occurrence."""
    if isinstance(exc, ValidationError):
        return True
    return isinstance(exc, DomainError) and exc.status_code != 503


#: Counts one failed try of a delivery and returns how many there have been.
type FailureCounter = Callable[[SurfaceWebhookReceivedEvent], Awaitable[int]]
#: Tells the sender of a delivery that it was lost.
type SenderTeller = Callable[
    [SurfaceWebhookReceivedEvent, UnitOfWorkFactory], Awaitable[None]
]


async def after_failed_delivery(
    received: SurfaceWebhookReceivedEvent,
    exc: BaseException,
    *,
    uow_factory: UnitOfWorkFactory,
    count_failure: FailureCounter | None = None,
    tell_sender: SenderTeller | None = None,
) -> None:
    """Count one failed try; on the last, tell the sender. Never raises."""
    count_failure = count_failure or _failed_tries
    tell_sender = tell_sender or _tell_sender
    try:
        if inbox_will_not_retry(exc) or (
            await count_failure(received) >= CONSUMER_ATTEMPTS
        ):
            await tell_sender(received, uow_factory)
    except (RedisError, OSError, SQLAlchemyError, *PLATFORM_TRANSPORT_ERRORS):
        logger.warning(
            "agent_surfaces.lost_delivery.sender_not_told.degraded",
            source=received.source,
            exc_info=True,
        )


async def _failed_tries(received: SurfaceWebhookReceivedEvent) -> int:
    key = f"surface:webhook-failures:{received.event_id}"
    redis = get_redis()
    count = int(await redis.incr(key))
    await redis.expire(key, _FAILURE_COUNT_TTL_SECONDS)
    return count


async def _tell_sender(
    received: SurfaceWebhookReceivedEvent, uow_factory: UnitOfWorkFactory
) -> None:
    platform = SurfacePlatform.from_source(received.source)
    if platform is None:
        return
    adapters = SurfacePlatformAdapterRegistry()
    adapter = adapters.get(platform)
    if adapter is None:
        return
    parsed = await adapter.parse_inbound_event(received.payload, received.headers)
    if parsed is None or not parsed.is_dm or not parsed.sender_external_user_id:
        return
    logger.warning(
        "agent_surfaces.lost_delivery.sender_told.degraded",
        source=received.source,
        surface_id=str(received.surface_id) if received.surface_id else None,
    )
    # A delivery to one surface's own webhook is that surface's bot, whose
    # credentials are found through its account; a shared one has none.
    account_id = None
    if received.surface_id is not None:
        async with uow_factory() as uow:
            surface = await SurfaceRepository(uow).get(received.surface_id)
        account_id = surface.account_id if surface is not None else None
    await SurfaceTurnStarter(
        uow_factory=uow_factory, adapter_registry=adapters
    ).execute_chat(
        SurfaceReplyContext(
            platform=platform,
            surface_id=received.surface_id,
            surface_account_id=account_id,
            event=parsed,
            reply_kind="processing_failed",
            reply_message=PROCESSING_FAILED_REPLY,
        )
    )
