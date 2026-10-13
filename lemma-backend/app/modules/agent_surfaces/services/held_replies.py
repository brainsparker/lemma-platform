"""Messages held for a member until they reopen a closed reply window.

WhatsApp takes free-form messages from a business only within 24 hours of the
person's last one. Past that, a message to a member goes as an approved
template asking them to reply (``member_reach``), and the message itself is
held here -- in the conversation's metadata, so it outlives any one worker --
until they do. Their reply reopens the window, and the next turn on that
conversation delivers what was held before anything else is said.

Held for a week. A message nobody came back for in that time is dropped when
they finally do, rather than delivered as if it were news.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversation_metadata_records as metadata_records,
)
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.caches import HELD_REPLY_CLAIMS, surface_caches
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)

logger = get_logger(__name__)

#: The conversation metadata key the held messages are written under.
HELD_REPLIES_KEY = "surface_held_replies"

#: How long a held message is still worth delivering.
HOLD_FOR = timedelta(days=7)
#: How long after asking one template is enough. Past it, a new message asks
#: again: the first may never have been seen.
ASK_AGAIN_AFTER = timedelta(days=1)


class HeldReply(BaseModel):
    message: str
    held_at: datetime


class HeldReplies(BaseModel):
    """What is waiting for a member to write again, oldest first."""

    #: When the template asking them to reply last went. Also what tells this
    #: set from a later one, so clearing it cannot clear a newer.
    asked_at: datetime
    replies: list[HeldReply] = Field(default_factory=list)

    def identity(self) -> dict[str, str | None]:
        return {"asked_at": self.model_dump(mode="json")["asked_at"]}


async def _held(uow: SqlAlchemyUnitOfWork, conversation_id: UUID) -> HeldReplies | None:
    stored = await metadata_records.conversation_metadata_record(
        uow, conversation_id, HELD_REPLIES_KEY
    )
    if stored is None:
        return None
    try:
        return HeldReplies.model_validate(stored)
    except ValidationError:
        logger.warning(
            "agent_surfaces.held_replies.unreadable.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
        return None


async def has_held_replies(uow: SqlAlchemyUnitOfWork, conversation_id: UUID) -> bool:
    """Whether anything waits on this conversation for the member to write."""
    held = await _held(uow, conversation_id)
    return held is not None and bool(held.replies)


async def needs_asking(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, *, now: datetime | None = None
) -> bool:
    """Whether the member should be asked to reply: nothing is held for them
    yet, or the last ask is old enough that it may never have been seen."""
    held = await _held(uow, conversation_id)
    return held is None or held.asked_at < (now or datetime.now(UTC)) - ASK_AGAIN_AFTER


async def hold_reply(
    uow: SqlAlchemyUnitOfWork,
    conversation_id: UUID,
    message: str,
    *,
    asked: bool,
    now: datetime | None = None,
) -> None:
    """Hold ``message`` beside anything already held. ``asked``: a template
    asking them to reply has just gone."""
    at = now or datetime.now(UTC)
    held = await _held(uow, conversation_id) or HeldReplies(asked_at=at)
    await agent_conversations.set_conversation_metadata_value(
        uow,
        conversation_id,
        HELD_REPLIES_KEY,
        HeldReplies(
            asked_at=at if asked else held.asked_at,
            replies=[*held.replies, HeldReply(message=message, held_at=at)],
        ),
    )


async def deliver_held_replies(
    *,
    adapter: SurfacePlatformAdapterPort,
    credentials: SurfaceCredentials,
    event: ParsedInboundSurfaceEvent,
    conversation_id: UUID,
    uow_factory: UnitOfWorkFactory,
    now: datetime | None = None,
) -> None:
    """Send what was held for the person who just wrote. Never raises.

    Claimed in Redis first, so two of their messages arriving together do not
    both deliver it; a send that fails gives the claim back and leaves the
    messages held for their next one.

    Only on a platform with a reply window: nothing is held anywhere else, and
    every inbound message would otherwise pay a read to find that out.
    """
    capabilities = get_platform_capabilities(event.platform.value)
    if capabilities is None or capabilities.reply_window_hours is None:
        return
    async with uow_factory() as uow:
        held = await _held(uow, conversation_id)
    if held is None:
        return
    claims = surface_caches.get(HELD_REPLY_CLAIMS)
    claim = f"{conversation_id}:{held.identity()['asked_at']}"
    try:
        if not await claims.set_raw_if_absent(claim, "delivering"):
            return
    except RedisError, OSError:
        logger.warning(
            "agent_surfaces.held_replies.claim_unavailable.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
        return
    fresh_since = (now or datetime.now(UTC)) - HOLD_FOR
    try:
        for reply in held.replies:
            if reply.held_at < fresh_since:
                continue
            await adapter.send_message(
                credentials=credentials, event=event, message=reply.message
            )
    except PLATFORM_TRANSPORT_ERRORS:
        logger.warning(
            "agent_surfaces.held_replies.not_delivered.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
        await claims.delete(claim)
        return
    await _forget(held, conversation_id, uow_factory)


async def _forget(
    held: HeldReplies, conversation_id: UUID, uow_factory: UnitOfWorkFactory
) -> None:
    try:
        async with uow_factory() as uow:
            await metadata_records.forget_conversation_metadata_record(
                uow, conversation_id, HELD_REPLIES_KEY, matching=held.identity()
            )
    except SQLAlchemyError:
        # Delivered, and the claim stands for its lifetime; past that, the
        # person's next message would deliver it again.
        logger.warning(
            "agent_surfaces.held_replies.not_forgotten.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )


__all__ = [
    "HELD_REPLIES_KEY",
    "HeldReplies",
    "deliver_held_replies",
    "hold_reply",
]
