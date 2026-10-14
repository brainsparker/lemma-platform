"""Messages held for a member until they reopen a closed reply window.

WhatsApp takes free-form messages from a business only within 24 hours of the
person's last one. Past that, a message to a member goes as an approved
template asking them to reply (``member_reach``), and the message itself is
held here -- in the conversation's metadata, so it outlives any one worker --
until they do. Their reply reopens the window: the transaction that writes it
turns what was held into ``surface_deliveries`` rows, which the destination
pump sends before anything else is said.

Held for a week. A message nobody came back for in that time is dropped when
they finally do, rather than delivered as if it were news.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversation_metadata_records as metadata_records,
)
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.deliveries import DeliveryContent
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    get_platform_capabilities,
)
from app.modules.agent_surfaces.services.surface_deliveries import (
    stage_conversation_message,
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


async def stage_held_replies(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    pod_id: UUID | None,
    platform: SurfacePlatform | str,
    now: datetime | None = None,
) -> int:
    """Write what was held for the person who just wrote as deliveries.

    In the transaction that writes their message, so the held messages are on
    record -- and cleared from the conversation -- exactly when the message
    that reopened the window is, and the destination pump sends them ahead of
    the answer that message will get. Keyed by the set's ``asked_at``, so a
    retried ingest writes them once.

    Only on a platform with a reply window: nothing is held anywhere else, and
    every inbound message would otherwise pay a read to find that out.
    """
    capabilities = get_platform_capabilities(
        platform.value if isinstance(platform, SurfacePlatform) else str(platform)
    )
    if capabilities is None or capabilities.reply_window_hours is None:
        return 0
    held = await _held(uow, conversation_id)
    if held is None:
        return 0
    fresh_since = (now or datetime.now(UTC)) - HOLD_FOR
    asked = held.identity()["asked_at"]
    staged = 0
    for index, reply in enumerate(held.replies):
        if reply.held_at < fresh_since:
            continue
        await stage_conversation_message(
            uow,
            idempotency_key=f"held:{conversation_id}:{asked}:{index}",
            conversation_id=conversation_id,
            pod_id=pod_id,
            content=DeliveryContent(text=reply.message, plain=True, purpose="held"),
        )
        staged += 1
    await metadata_records.forget_conversation_metadata_record(
        uow, conversation_id, HELD_REPLIES_KEY, matching=held.identity()
    )
    return staged


__all__ = [
    "HELD_REPLIES_KEY",
    "HeldReplies",
    "stage_held_replies",
    "hold_reply",
]
