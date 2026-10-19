"""What a surface heard, as lines on the channel it happened in.

Every message a person sends the pod is a line of the channel its place
belongs to (``app.modules.channels``): the timeline the pod's members read,
and the background a later run is handed. Written as the message enters the
person's conversation. The agent's answer is a line too, posted before it is
sent and carried by its delivery (``run_reply_lines``).

A group's messages are written earlier too, by ``group_log``, as they arrive:
most of a group is not said to the bot, and all of it is what "what did we
decide yesterday?" asks about. The same platform message is one line however
many times it is written.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import channel_conversations
from app.modules.agent.contracts.audience import Audience
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.services.line_markup import Bot, LineMarkup
from app.modules.channels.contracts.deliveries import deliveries_of, delivery_sent
from app.modules.channels.contracts.lines import thread_root_at
from app.modules.channels.contracts import (
    ChannelMessage,
    ContactsHome,
    NewLine,
    Place,
    Principal,
    Sender,
    Source,
    SourceKind,
    post,
    source_by_id,
    source_for,
)


#: Platforms whose channels have threads, kept as threads on the timeline.
THREADED = frozenset({"SLACK", "TEAMS"})

#: A web chat's platform, as its channel source names it.
WEB = "WEB"


def reply_ref_of(event: ParsedInboundSurfaceEvent) -> dict[str, object]:
    """What answers a heard line in place -- its thread, the message replied
    to -- without the platform's whole payload, which a reply never reads and
    a timeline should not keep on every line."""
    return event.model_dump(mode="json", exclude={"raw_payload"})


def web_place(*, widget_id: UUID, session_id: UUID) -> Place:
    """A visitor's chat on one widget: keyed by the widget and their session.
    The widget is its own account; no surface carries it."""
    return Place(
        surface_id=None,
        platform=WEB,
        account_key=f"widget:{widget_id}",
        kind=SourceKind.PRIVATE,
        external_channel_id=str(widget_id),
        external_user_id=str(session_id),
    )


class ChannelLines(Protocol):
    """Where a surface writes what it heard, as channel lines."""

    async def heard(self, context: SurfaceChatContext, *, text: str) -> None: ...


class ChannelLinesInDatabase:
    """The lines, on the channels' timelines."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    async def heard(self, context: SurfaceChatContext, *, text: str) -> None:
        await note_heard(self.uow, context, text=text)


#: The lines over one unit of work, for a writer that opens its own scopes.
type ChannelLinesFor = Callable[[SqlAlchemyUnitOfWork], ChannelLines]


async def note_heard(
    uow: SqlAlchemyUnitOfWork, context: SurfaceChatContext, *, text: str
) -> None:
    """The message this run answers, as its sender's line in the channel.

    ``text`` is what the conversation is given -- a voice note's words, not
    the empty body it arrived with.
    """
    if context.source_id is None:
        return
    source = await source_by_id(uow, context.source_id)
    if source is None:
        return
    name = context.message_metadata.sender_display_name
    contact_id = context.audience.contact_id
    sender = (
        Sender.contact(contact_id, display_name=name)
        if contact_id is not None
        else Sender.member(context.message_user_id, display_name=name)
    )
    event = context.event
    said = await LineMarkup(uow).read(
        event,
        pod_id=source.pod_id,
        channel_id=source.channel_id,
        text=text,
        bot=Bot(surface_id=context.surface_id, name=context.agent_display_name),
    )
    await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=sender,
            text=said.text,
            parts=said.parts,
            source_id=source.id,
            conversation_id=context.conversation_id,
            external_message_id=context.message_external_message_id,
            external_thread_id=event.external_thread_id,
            # A Slack or Teams thread is a thread here too.
            thread_root_id=await _thread_root(
                uow,
                source,
                external_thread_id=event.external_thread_id,
                external_message_id=context.message_external_message_id,
            ),
            # What answers this line in place: its thread, the message replied to.
            reply_ref=reply_ref_of(event),
        ),
        mentions=said.mentions,
        mentioned_agents=said.agents,
    )


async def _thread_root(
    uow: SqlAlchemyUnitOfWork,
    source: Source,
    *,
    external_thread_id: str | None,
    external_message_id: str | None = None,
) -> UUID | None:
    """The line a reply in a shared Slack or Teams place hangs from."""
    if source.kind is not SourceKind.SHARED or source.platform not in THREADED:
        return None
    return await thread_root_at(
        uow,
        source_id=source.id,
        external_thread_id=external_thread_id,
        external_message_id=external_message_id,
    )


async def note_heard_on_web(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    widget_id: UUID,
    session_id: UUID,
    contact_id: UUID,
    conversation_id: UUID,
    text: str,
    display_name: str | None,
) -> None:
    """A visitor's message, as their line in their contact channel.

    The web chat is one more source of that channel, and the visitor's
    conversation is placed there with it.
    """
    source = await source_for(
        uow,
        pod_id=pod_id,
        place=web_place(widget_id=widget_id, session_id=session_id),
        home=ContactsHome(contact_id=contact_id),
    )
    await channel_conversations.place_in_channel(
        uow, conversation_id=conversation_id, channel_id=source.channel_id
    )
    await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=Sender.contact(contact_id, display_name=display_name),
            text=text,
            source_id=source.id,
            conversation_id=conversation_id,
        ),
    )


def answered_for(audience: Audience, user_id: UUID) -> Principal:
    """Whom an answer was for, and so whose access it was made with.

    A contact's conversation answers the contact, from what the pod made
    Public; any other answers the member whose conversation it is, with their
    own access -- the one member a group's page shows its words to.
    """
    if audience.contact_id is not None:
        return Principal.contact(audience.contact_id)
    return Principal.member(user_id)


async def shown_on_web(
    uow: SqlAlchemyUnitOfWork, line: ChannelMessage, source: Source
) -> None:
    """A line for a web chat, which shows it from the conversation: its
    delivery there is ``SENT`` at once, and nothing is sent. Every line for a
    place has its delivery row, so the line says where it went the same way
    whatever the platform."""
    for delivery in await deliveries_of(uow, [line.id]):
        if delivery.source_id == source.id:
            await delivery_sent(
                uow, delivery.id, platform=source.platform, external_message_id=None
            )
