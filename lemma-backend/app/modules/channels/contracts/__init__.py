"""What other modules may ask of channels.

A surface hears something in a place: it asks for that place's source (made,
with the right channel, the first time), adds the line to the timeline, and
reads back what a run may be shown. An agent posts its answer. Nothing here
lists a pod's channels -- that is a member's, through this module's API.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import (
    Channel,
    ChannelSettings,
    ChannelKind,
    ChannelMessage,
    Interaction,
    InteractionKind,
    InteractionState,
    NotifyLevel,
    Participant,
    Principal,
    Sender,
    SenderKind,
    Source,
    SourceKind,
)
from app.modules.channels.domain.projection import Reader, may_read, withheld_from
from app.modules.channels.infrastructure.lines import NewLine, NewQuestion
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.speakers import (
    MAX_SPEAKERS,
    Speaker,
    SpeakerReads,
)
from app.modules.channels.services.places import (
    ChannelPlaces,
    ContactsHome,
    GroupHome,
    Home,
    MembersHome,
    Place,
)
from app.modules.channels.services.timeline import ChannelNotFound, Timeline

__all__ = [
    "Channel",
    "ChannelSettings",
    "ChannelKind",
    "ChannelMessage",
    "ChannelNotFound",
    "ContactsHome",
    "GroupHome",
    "Home",
    "Interaction",
    "NotifyLevel",
    "InteractionKind",
    "InteractionState",
    "MembersHome",
    "NewLine",
    "NewQuestion",
    "Participant",
    "Place",
    "Principal",
    "Reader",
    "Sender",
    "SenderKind",
    "Source",
    "SourceKind",
    "Speaker",
    "background",
    "call_for_person",
    "channel_by_id",
    "contact_channel",
    "direct_channel",
    "hand_back",
    "handler_of",
    "may_read",
    "withheld_from",
    "participant_of",
    "participants_of",
    "post",
    "source_by_id",
    "source_for",
    "sources_of",
    "speakers_in",
]


async def source_for(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, place: Place, home: Home
) -> Source:
    """The source for an outside place, with its channel made the first time."""
    return await ChannelPlaces(uow).source_for(pod_id=pod_id, place=place, home=home)


async def source_by_id(uow: SqlAlchemyUnitOfWork, source_id: UUID) -> Source | None:
    return await ChannelRepository(uow.session).get_source(source_id)


async def channel_by_id(uow: SqlAlchemyUnitOfWork, channel_id: UUID) -> Channel | None:
    return await ChannelRepository(uow.session).get(channel_id)


async def call_for_person(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, reason: str | None
) -> None:
    """The channel asks for a person -- flagged until somebody steps in."""
    await ChannelRepository(uow.session).call_for_person(channel_id, reason=reason)


async def participant_of(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, principal: Principal
) -> Participant | None:
    """Somebody's seat in a channel -- how they hear of it -- if they have one."""
    return await ChannelRepository(uow.session).participant(
        channel_id=channel_id, principal=principal
    )


async def direct_channel(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, user_id: UUID
) -> Channel:
    """The member's DIRECT channel with the pod, made if there is none yet."""
    return await ChannelRepository(uow.session).direct(pod_id=pod_id, user_id=user_id)


async def contact_channel(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_id: UUID
) -> Channel:
    """The contact's CONTACT channel, made if there is none yet."""
    return await ChannelRepository(uow.session).for_contact(
        pod_id=pod_id, contact_id=contact_id
    )


async def post(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel_id: UUID,
    sender: Sender,
    text: str,
    source_id: UUID | None = None,
    members_only: bool = False,
    conversation_id: UUID | None = None,
    external_message_id: str | None = None,
    external_thread_id: str | None = None,
    reply_ref: Mapping[str, object] | None = None,
    parts: Sequence[Mapping[str, object]] = (),
    agent_run_id: UUID | None = None,
    reply_to_id: UUID | None = None,
    thread_root_id: UUID | None = None,
    question: NewQuestion | None = None,
    mentions: Sequence[Principal] = (),
    mentioned_agents: Sequence[UUID] = (),
) -> ChannelMessage | None:
    """Add a line to the channel's timeline; the same platform message once.

    ``None`` for a line with nothing in it. Raises ``ChannelNotFound`` when the
    channel is gone. ``question`` makes the line ask one person something,
    which they answer in place (``answer_question``).
    """
    channel = await ChannelRepository(uow.session).get(channel_id)
    if channel is None:
        raise ChannelNotFound(str(channel_id))
    return await Timeline(uow).post(
        channel,
        sender=sender,
        text=text,
        source_id=source_id,
        line=NewLine(
            members_only=members_only,
            conversation_id=conversation_id,
            external_message_id=external_message_id,
            external_thread_id=external_thread_id,
            reply_ref=reply_ref,
            parts=tuple(parts),
            agent_run_id=agent_run_id,
            reply_to_id=reply_to_id,
            thread_root_id=thread_root_id,
        ),
        question=question,
        mentions=mentions,
        mentioned_agents=mentioned_agents,
    )


async def background(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, reader: Reader, limit: int
) -> list[ChannelMessage]:
    """The recent lines a run reading as ``reader`` may be shown, oldest first."""
    return await Timeline(uow).background(channel_id, reader=reader, limit=limit)


async def sources_of(uow: SqlAlchemyUnitOfWork, channel_id: UUID) -> list[Source]:
    return await ChannelRepository(uow.session).sources_of(channel_id)


async def participants_of(
    uow: SqlAlchemyUnitOfWork, channel_id: UUID
) -> list[Participant]:
    return await ChannelRepository(uow.session).participants(channel_id)


async def hand_back(uow: SqlAlchemyUnitOfWork, channel_id: UUID) -> None:
    """The member who stepped in lets go: the channel's agents answer again."""
    await ChannelRepository(uow.session).set_handler(channel_id, None)


async def handler_of(uow: SqlAlchemyUnitOfWork, channel_id: UUID) -> UUID | None:
    """The member answering this channel by hand, or ``None`` for its agents."""
    channel = await ChannelRepository(uow.session).get(channel_id)
    return channel.handler_user_id if channel else None


async def speakers_in(
    uow: SqlAlchemyUnitOfWork,
    channel_ids: Collection[UUID],
    *,
    limit: int = MAX_SPEAKERS,
) -> list[Speaker]:
    """Who spoke in these channels, once per channel, most recent first."""
    return await SpeakerReads(uow.session).speakers(channel_ids, limit=limit)
