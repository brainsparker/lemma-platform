"""What other modules may ask of channels.

A surface hears something in a place: it asks for that place's source (made,
with the right channel, the first time), adds the line to the timeline, and
reads back what a run may be shown. An agent posts its answer. Nothing here
lists a pod's channels -- that is a member's, through this module's API.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Participant,
    Principal,
    Sender,
    SenderKind,
    Source,
    SourceKind,
)
from app.modules.channels.domain.projection import Reader, may_read
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
    "ChannelKind",
    "ChannelMessage",
    "ChannelNotFound",
    "ContactsHome",
    "GroupHome",
    "Home",
    "MembersHome",
    "Participant",
    "Place",
    "Principal",
    "Reader",
    "Sender",
    "SenderKind",
    "Source",
    "SourceKind",
    "Speaker",
    "answering_source",
    "background",
    "channel_by_id",
    "contact_channel",
    "handler_of",
    "may_read",
    "participants_of",
    "post",
    "shared_sources",
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
) -> ChannelMessage | None:
    """Add a line to the channel's timeline; the same platform message once.

    ``None`` for a line with nothing in it. Raises ``ChannelNotFound`` when the
    channel is gone.
    """
    channel = await ChannelRepository(uow.session).get(channel_id)
    if channel is None:
        raise ChannelNotFound(str(channel_id))
    return await Timeline(uow).post(
        channel,
        sender=sender,
        text=text,
        source_id=source_id,
        members_only=members_only,
        conversation_id=conversation_id,
        external_message_id=external_message_id,
        external_thread_id=external_thread_id,
    )


async def background(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, reader: Reader, limit: int
) -> list[ChannelMessage]:
    """The recent lines a run reading as ``reader`` may be shown, oldest first."""
    return await Timeline(uow).background(channel_id, reader=reader, limit=limit)


async def answering_source(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> Source | None:
    """Where the conversation's answer goes: the source its person last spoke on.

    A conversation spans every source of its channel, and an answer goes back
    only to the one it answers -- never mirrored to the others.
    """
    return await ChannelRepository(uow.session).latest_heard(conversation_id)


async def sources_of(uow: SqlAlchemyUnitOfWork, channel_id: UUID) -> list[Source]:
    return await ChannelRepository(uow.session).sources_of(channel_id)


async def participants_of(
    uow: SqlAlchemyUnitOfWork, channel_id: UUID
) -> list[Participant]:
    return await ChannelRepository(uow.session).participants(channel_id)


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


async def shared_sources(
    uow: SqlAlchemyUnitOfWork, places: Collection[tuple[UUID, str]]
) -> dict[tuple[UUID, str], Source]:
    """The source each ``(surface_id, chat id)`` is, for those heard so far."""
    return await SpeakerReads(uow.session).shared_sources(places)
