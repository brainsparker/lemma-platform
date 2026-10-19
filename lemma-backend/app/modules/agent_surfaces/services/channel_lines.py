"""What a surface heard and said, as lines on the channel it happened in.

Every message a person sends the pod, and every answer its agent delivers, is a
line of the channel its place belongs to (``app.modules.channels``): the
timeline the pod's members read, and the background a later run is handed.
Written at the two moments a surface knows a line is real -- a message as it
enters the person's conversation, an answer once the platform has taken it.

A group's messages are written earlier too, by ``group_log``, as they arrive:
most of a group is not said to the bot, and all of it is what "what did we
decide yesterday?" asks about. The same platform message is one line however
many times it is written.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import channel_conversations
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.audience import Audience
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.services.surface_route_types import (
    SurfaceEgressTarget,
)
from app.modules.channels.contracts.lines import thread_root_at
from app.modules.channels.contracts import (
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


#: Delivery metadata naming the line being delivered, which is on the timeline
#: already: the delivery writes no second one.
ALREADY_A_LINE = "channel_message_id"

#: Platforms whose channels have threads, kept as threads on the timeline.
THREADED = frozenset({"SLACK", "TEAMS"})

#: A web chat's platform, as its channel source names it.
WEB = "WEB"


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
    """Where a surface writes what it heard and said, as channel lines."""

    async def heard(self, context: SurfaceChatContext, *, text: str) -> None: ...

    async def said(
        self,
        *,
        target: SurfaceEgressTarget,
        conversation_id: UUID,
        text: str,
        agent_display_name: str | None = None,
    ) -> None: ...


class ChannelLinesInDatabase:
    """The lines, on the channels' timelines."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    async def heard(self, context: SurfaceChatContext, *, text: str) -> None:
        await note_heard(self.uow, context, text=text)

    async def said(
        self,
        *,
        target: SurfaceEgressTarget,
        conversation_id: UUID,
        text: str,
        agent_display_name: str | None = None,
    ) -> None:
        await note_said(
            self.uow,
            target=target,
            conversation_id=conversation_id,
            text=text,
            agent_display_name=agent_display_name,
        )


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
    await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=sender,
            text=text,
            source_id=source.id,
            conversation_id=context.conversation_id,
            external_message_id=context.message_external_message_id,
            external_thread_id=context.event.external_thread_id,
            # What answers this line in place: its thread, the message replied to.
            reply_ref=context.event.model_dump(mode="json"),
        ),
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


async def note_said(
    uow: SqlAlchemyUnitOfWork,
    *,
    target: SurfaceEgressTarget,
    conversation_id: UUID,
    text: str,
    agent_display_name: str | None = None,
) -> None:
    """What the agent delivered, as its line in the channel, said for the
    person it answered. After the platform took it, so the timeline never
    holds a line nobody saw."""
    if target.conversation_user_id is None:
        return
    source = target.source
    agent_id = effective_agent_id(
        target.agent_id or target.surface.agent_id, pod_id=target.pod_id
    )
    thread_root_id = (
        await thread_root_at(
            uow, source_id=source.id, external_thread_id=target.event.external_thread_id
        )
        if source.kind is SourceKind.SHARED and source.platform in THREADED
        else None
    )
    await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=Sender.agent(
                agent_id,
                on_behalf_of=answered_for(target.audience, target.conversation_user_id),
                display_name=agent_display_name,
            ),
            text=text,
            source_id=source.id,
            conversation_id=conversation_id,
            # The thread it went to, so the place knows the agent is in it.
            external_thread_id=target.event.external_thread_id,
            # A Slack or Teams thread is a thread here too.
            thread_root_id=thread_root_id,
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


async def note_answered_on(
    uow: SqlAlchemyUnitOfWork, *, source: Source, conversation_id: UUID, text: str
) -> None:
    """An answer given where no platform delivers it -- the web chat -- as the
    agent's line, said for the person the conversation answers."""
    conversation = await agent_conversations.surface_conversation(uow, conversation_id)
    if conversation is None:
        return
    await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=Sender.agent(
                effective_agent_id(conversation.agent_id, pod_id=conversation.pod_id),
                on_behalf_of=answered_for(conversation.audience, conversation.user_id),
            ),
            text=text,
            source_id=source.id,
            conversation_id=conversation_id,
        ),
    )
