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
from app.modules.agent.contracts.audience import Audience
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.services.surface_route_types import (
    SurfaceEgressTarget,
)
from app.modules.channels.contracts import (
    Principal,
    Sender,
    post,
    source_by_id,
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
        sender=sender,
        text=text,
        source_id=source.id,
        conversation_id=context.conversation_id,
        external_message_id=context.message_external_message_id,
    )


async def note_said(
    uow: SqlAlchemyUnitOfWork,
    *,
    target: SurfaceEgressTarget,
    conversation_id: UUID,
    text: str,
    agent_display_name: str | None = None,
) -> None:
    """What the agent delivered, as its line in the channel, said for whom.

    After the platform took it, so the timeline never holds a line nobody saw.
    """
    source_id = target.link.source_id
    if source_id is None or target.conversation_user_id is None:
        return
    source = await source_by_id(uow, source_id)
    if source is None:
        return
    agent_id = effective_agent_id(
        target.link.routed_agent_id or target.surface.agent_id, pod_id=target.pod_id
    )
    await post(
        uow,
        channel_id=source.channel_id,
        sender=Sender.agent(
            agent_id,
            on_behalf_of=answered_for(target.audience, target.conversation_user_id),
            display_name=agent_display_name,
        ),
        text=text,
        source_id=source.id,
        conversation_id=conversation_id,
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
