"""Pushes the hourly budget held back, told to the member as one message.

A member who has had ``PushBudget``'s worth of pushes from a pod this hour is
not pushed again: the line waits in Lemma and its delivery is ``SKIPPED``
with ``HELD_FOR_DIGEST``. Being held must not mean being forgotten, so the
first held push of a channel queues one digest for that member and channel,
``DIGEST_AFTER`` later; pushes held meanwhile join it (the item's dedup key).
The digest is a note in the member's own channel that names them -- so it is
pushed, and as a line that names them it is never held itself -- saying how
many lines came in which channel. The held deliveries then read
``DIGESTED``.

What names a member or asks them something is never held
(``push_is_urgent``), so a digest only ever covers lines they could leave for
later.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter, pod_namespace
from app.core.log.log import get_logger
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_PUSH_DIGEST,
    push_digest_key,
)
from app.modules.channels.contracts import channel_by_id
from app.modules.channels.contracts.deliveries import delivery_reasoned, held_pushes

logger = get_logger(__name__)

#: How long after the first held push of a channel its digest goes: lines
#: held meanwhile go in the same one.
DIGEST_AFTER = timedelta(minutes=30)

HELD_FOR_DIGEST = (
    "They have had a lot from this pod this hour; it waits in Lemma, and they "
    "will be told of it in one message."
)
DIGESTED = (
    "They had had a lot from this pod that hour; it waited in Lemma, and they "
    "were told of it in one message."
)


class PushDigest(BaseModel):
    pod_id: UUID
    recipient_user_id: UUID
    channel_id: UUID


async def hold_for_digest(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    recipient_user_id: UUID,
    channel_id: UUID,
) -> None:
    """Queue the digest that will tell them of this channel's held pushes;
    one already queued takes this one too."""
    await WorkWriter.of(uow).add(
        SURFACE_PUSH_DIGEST,
        pod_namespace(pod_id),
        PushDigest(
            pod_id=pod_id, recipient_user_id=recipient_user_id, channel_id=channel_id
        ),
        dedup_key=push_digest_key(recipient_user_id, channel_id),
        delay=DIGEST_AFTER,
    )


def digest_text(count: int, *, channel_title: str | None) -> str:
    where = f"in {channel_title}" if channel_title else "in one of your channels"
    lines = "a line" if count == 1 else f"{count} lines"
    return (
        f"While you were getting a lot from this pod, {lines} came {where} "
        "without a message to you. They are waiting in Lemma."
    )


async def send_digest(uow: SqlAlchemyUnitOfWork, digest: PushDigest) -> int:
    """Tell the member of the pushes held for this channel, once; how many
    lines that was (none when a digest already told them)."""
    from app.modules.agent_surfaces.services.direct_messages import tell_member

    held = await held_pushes(
        uow,
        recipient_user_id=digest.recipient_user_id,
        channel_id=digest.channel_id,
        reason=HELD_FOR_DIGEST,
    )
    if not held:
        return 0
    channel = await channel_by_id(uow, digest.channel_id)
    await delivery_reasoned(uow, [delivery.id for delivery in held], reason=DIGESTED)
    await tell_member(
        uow,
        pod_id=digest.pod_id,
        user_id=digest.recipient_user_id,
        text=digest_text(
            len(held), channel_title=channel.title if channel is not None else None
        ),
        urgent=True,
    )
    logger.info(
        "agent_surfaces.push_digests.sent.observed",
        lines=len(held),
        channel_id=str(digest.channel_id),
    )
    return len(held)


__all__ = [
    "DIGESTED",
    "DIGEST_AFTER",
    "HELD_FOR_DIGEST",
    "PushDigest",
    "digest_text",
    "hold_for_digest",
    "send_digest",
]
