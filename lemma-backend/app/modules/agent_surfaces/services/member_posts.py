"""A member's line, carried to the outside place it was written for.

A member writes in a channel in Lemma; the line names one of the channel's
places (``channels.services.messages`` chose it), and this takes it there as
the pod's bot -- under the member's name on the line, which the timeline
already holds. Each attempt is recorded on the line: sent, or why not.

Writing to a contact keeps the rules a business is held to, and a contact
asked for:

* **Never a handle they unsubscribed**, and email always carries a line, and a
  link, saying how to stop.
* **WhatsApp only within 24 hours** of their last message: past it Meta takes
  only pre-approved templates, which are not built here.
* **At most ``surface_contact_follow_ups_per_contact_per_day``** lines a day.
* **A web chat** has nobody to hand it to: it waits in their chat for their
  next visit.

What was written to a contact goes into their conversation too, marked as a
member's (``append_follow_up``), so the agent answering them next knows a
person said it -- and so does their web chat.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.contact_conversations import append_follow_up
from app.modules.agent_surfaces.composition import build_surface_egress
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.services.channel_lines import (
    ALREADY_A_LINE,
    WEB,
    WRITTEN_BY_MEMBER,
)
from app.modules.agent_surfaces.services.contact_rules import (
    ContactRefused,
    handle_on,
    unsubscribe_line,
    within_the_days_lines,
)
from app.modules.agent_surfaces.services.contact_windows import ContactWindows
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    ChannelMessage,
    SenderKind,
    Source,
    channel_by_id,
    source_by_id,
)
from app.modules.channels.contracts.deliveries import DeliveryStatus, record_delivery
from app.modules.channels.contracts.lines import line_by_id
from app.modules.channels.contracts.places import conversation_at
from app.modules.contacts.contracts import IdentityKind, contact_handles

logger = get_logger(__name__)


def delivers(line: ChannelMessage | None) -> bool:
    """A member's line for an outside place: not a note, not already there."""
    return (
        line is not None
        and line.sender.kind is SenderKind.MEMBER
        and line.sender.user_id is not None
        and line.source_id is not None
        and not line.members_only
        and not line.is_deleted
        and line.external_message_id is None
    )


async def deliver_member_line(
    uow_factory: UnitOfWorkFactory,
    *,
    message_id: UUID,
    windows: ContactWindows | None = None,
) -> None:
    async with uow_factory() as uow:
        line = await line_by_id(uow, message_id)
        if not delivers(line):
            return
        assert line is not None and line.source_id is not None
        channel = await channel_by_id(uow, line.channel_id)
        source = await source_by_id(uow, line.source_id)
        conversation_id = (
            await conversation_at(uow, source.id) if source is not None else None
        )
    if channel is None or source is None:
        return
    try:
        text = await _ready(uow_factory, channel, source, line, windows)
    except ContactRefused as refused:
        await _record(
            uow_factory, line, source, DeliveryStatus.SKIPPED, refused.message
        )
        return
    if conversation_id is None:
        await _record(
            uow_factory,
            line,
            source,
            DeliveryStatus.SKIPPED,
            "The bot has not talked with anybody there yet.",
        )
        return
    if source.platform == WEB:
        # Nobody to hand it to: it waits in their web chat for the next visit.
        await _to_their_conversation(
            uow_factory, channel, conversation_id, line, text, None
        )
        await _record(uow_factory, line, source, DeliveryStatus.SENT, None)
        return
    sent = await _send(uow_factory, conversation_id, source, line, text)
    await _to_their_conversation(
        uow_factory, channel, conversation_id, line, text, sent
    )
    await _record(
        uow_factory,
        line,
        source,
        DeliveryStatus.SENT if sent else DeliveryStatus.FAILED,
        None if sent else f"{source.platform} did not take it. Try again later.",
    )


async def _ready(
    uow_factory: UnitOfWorkFactory,
    channel: Channel,
    source: Source,
    line: ChannelMessage,
    windows: ContactWindows | None,
) -> str:
    """The text that goes, once a contact's rules allow it to; or a refusal."""
    if channel.kind is not ChannelKind.CONTACT or channel.contact_id is None:
        return line.text
    async with uow_factory() as uow:
        handles = await contact_handles(
            uow, pod_id=channel.pod_id, contact_id=channel.contact_id
        )
    handle = handle_on(source.platform, handles, datetime.now(timezone.utc))
    await within_the_days_lines(windows or ContactWindows(), channel.contact_id)
    if handle is not None and handle.kind is IdentityKind.EMAIL:
        return line.text + unsubscribe_line(handle)
    return line.text


async def _send(
    uow_factory: UnitOfWorkFactory,
    conversation_id: UUID,
    source: Source,
    line: ChannelMessage,
    text: str,
) -> bool:
    """Hand it to the platform at its place; whether the platform took it."""
    try:
        async with uow_factory() as uow:
            sent = await build_surface_egress(uow).send_agent_message_for_conversation(
                conversation_id=conversation_id,
                message=text,
                metadata={
                    WRITTEN_BY_MEMBER: str(line.sender.user_id),
                    ALREADY_A_LINE: str(line.id),
                },
                at=source,
            )
            await uow.commit()
    except platform_transport_errors() as exc:
        logger.warning(
            "agent_surfaces.member_posts.send_failed.degraded",
            platform=source.platform,
            error_type=type(exc).__name__,
        )
        return False
    return bool(sent)


async def _to_their_conversation(
    uow_factory: UnitOfWorkFactory,
    channel: Channel,
    conversation_id: UUID,
    line: ChannelMessage,
    text: str,
    delivered: bool | None,
) -> None:
    """A contact's conversation hears what a member wrote them, as theirs."""
    if channel.kind is not ChannelKind.CONTACT or line.sender.user_id is None:
        return
    async with uow_factory() as uow:
        await append_follow_up(
            uow,
            conversation_id=conversation_id,
            message=text,
            sent_by_user_id=line.sender.user_id,
            delivered=delivered,
        )
        await uow.commit()


async def _record(
    uow_factory: UnitOfWorkFactory,
    line: ChannelMessage,
    source: Source,
    status: DeliveryStatus,
    error: str | None,
) -> None:
    async with uow_factory() as uow:
        if line.pod_id is None:
            return
        await record_delivery(
            uow,
            message_id=line.id,
            pod_id=line.pod_id,
            platform=source.platform,
            status=status,
            source_id=source.id,
            error=error,
        )
        await uow.commit()
    logger.info(
        "agent_surfaces.member_posts.delivered.observed",
        platform=source.platform,
        status=status.value,
    )
