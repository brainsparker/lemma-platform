"""What a surface may ask of channels about places: where something is heard,
how to answer it, and a shared place's settings.

A place is a channel source, in one external account. A surface finds the
place a message came from, notes what it heard there, and asks where a
conversation's answer goes. Every lookup is scoped to the surfaces asking (or
to one pod), so one pod's bot never finds or forgets another pod's place.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import Source
from app.modules.channels.infrastructure.accounts import AccountRepository
from app.modules.channels.infrastructure.source_repository import (
    GROUP_SETTINGS,
    SourceRepository,
)
from app.modules.channels.services.places import ChannelPlaces, Place

__all__ = [
    "GROUP_SETTINGS",
    "ReplyAddress",
    "addressed_at",
    "change_place",
    "confirm_group",
    "conversation_at",
    "find_place",
    "forget_chat",
    "forget_pending_group",
    "heard_at",
    "open_pending_group",
    "owned_place",
    "people_places",
    "person_places",
    "place_by_request",
    "places_hearing",
    "places_of",
    "reply_address",
    "shared_places_of_pod",
    "surfaces_hearing",
]


@dataclass(frozen=True, slots=True)
class ReplyAddress:
    """Where a conversation's answer goes, and what answers it in place."""

    source: Source
    ref: dict[str, object] | None


async def find_place(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, place: Place
) -> Source | None:
    """The pod's source for a place, if it has been heard before. Makes nothing
    but the account."""
    account = await AccountRepository(uow.session).account_for(
        pod_id=pod_id,
        platform=place.platform,
        account_key=place.account_key,
        surface_id=place.surface_id,
    )
    return await SourceRepository(uow.session).find(
        account_id=account.id,
        external_channel_id=place.external_channel_id,
        external_user_id=place.external_user_id,
    )


async def heard_at(
    uow: SqlAlchemyUnitOfWork, source_id: UUID, delivery_ref: Mapping[str, object]
) -> None:
    """The surface heard this place just now; ``delivery_ref`` is how to answer."""
    await SourceRepository(uow.session).heard(source_id, delivery_ref)


async def addressed_at(
    uow: SqlAlchemyUnitOfWork, source_id: UUID, delivery_ref: Mapping[str, object]
) -> None:
    """How to write to a place nobody there has spoken in yet: a cold email."""
    await SourceRepository(uow.session).addressed(source_id, delivery_ref)


async def reply_address(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> ReplyAddress | None:
    """Where this conversation's answer goes, or None if it is said nowhere."""
    found = await SourceRepository(uow.session).reply_address(conversation_id)
    return ReplyAddress(source=found[0], ref=found[1]) if found else None


async def places_hearing(
    uow: SqlAlchemyUnitOfWork,
    *,
    external_channel_id: str,
    surface_ids: Collection[UUID] | None,
    platform: str | None = None,
) -> list[Source]:
    """The shared places on one chat, among these surfaces' accounts. ``None``
    for every account on the platform: only where the request's signature
    proved no narrower scope."""
    return await SourceRepository(uow.session).hearing(
        external_channel_id=external_channel_id,
        surface_ids=surface_ids,
        platform=platform,
    )


async def person_places(
    uow: SqlAlchemyUnitOfWork,
    *,
    platform: str,
    external_user_id: str,
    surface_ids: Collection[UUID] | None = None,
    pod_id: UUID | None = None,
    limit: int = 10,
) -> list[Source]:
    """One person's private places on a platform, freshest first, among these
    surfaces or in one pod (one of the two is required)."""
    return await SourceRepository(uow.session).person(
        platform=platform,
        external_user_id=external_user_id,
        surface_ids=surface_ids,
        pod_id=pod_id,
        limit=limit,
    )


async def people_places(
    uow: SqlAlchemyUnitOfWork,
    *,
    platform: str,
    external_user_ids: Collection[str],
    surface_id: UUID,
    pod_id: UUID,
) -> dict[str, Source]:
    """Each person's freshest private place on one surface, in one pod."""
    return await SourceRepository(uow.session).people(
        platform=platform,
        external_user_ids=external_user_ids,
        surface_id=surface_id,
        pod_id=pod_id,
    )


async def places_of(uow: SqlAlchemyUnitOfWork, channel_id: UUID) -> list[Source]:
    """A channel's places, the one heard from most recently first."""
    return await SourceRepository(uow.session).of_channel(channel_id)


async def shared_places_of_pod(
    uow: SqlAlchemyUnitOfWork,
    pod_id: UUID,
    *,
    limit: int = 200,
    surface_id: UUID | None = None,
) -> list[Source]:
    """The groups and shared channels the pod's bots are in; one bot's when named."""
    return await SourceRepository(uow.session).shared_of_pod(
        pod_id, limit=limit, surface_id=surface_id
    )


async def conversation_at(
    uow: SqlAlchemyUnitOfWork,
    source_id: UUID,
    *,
    sender_external_id: str | None = None,
    external_thread_id: str | None = None,
) -> UUID | None:
    """The conversation that last took up something a person said here."""
    return await SourceRepository(uow.session).conversation_at(
        source_id,
        sender_external_id=sender_external_id,
        external_thread_id=external_thread_id,
    )


async def place_by_request(
    uow: SqlAlchemyUnitOfWork,
    *,
    request_id: str,
    surface_ids: Collection[UUID] | None = None,
    platform: str | None = None,
) -> Source | None:
    """A pending group by the platform's request id, on these surfaces or --
    for a platform whose request ids are unique everywhere -- that platform."""
    return await SourceRepository(uow.session).by_request_on_surfaces(
        request_id=request_id, surface_ids=surface_ids, platform=platform
    )


async def surfaces_hearing(
    uow: SqlAlchemyUnitOfWork, *, platform: str, external_channel_id: str
) -> list[UUID]:
    """The surfaces whose bots are in this chat -- to route what arrives there."""
    return await SourceRepository(uow.session).surfaces_hearing(
        platform=platform, external_channel_id=external_channel_id
    )


async def owned_place(
    uow: SqlAlchemyUnitOfWork, *, surface_id: UUID, owner_user_id: UUID, title: str
) -> Source | None:
    """A shared place this member answers for on this surface, by its title."""
    return await SourceRepository(uow.session).owned(
        surface_id=surface_id, owner_user_id=owner_user_id, title=title
    )


async def open_pending_group(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    place: Place,
    title: str,
    owner_user_id: UUID,
    request_id: str,
    answers_outsiders: bool,
) -> Source:
    """A group the bot asked a platform to create, with its channel made now."""
    return await ChannelPlaces(uow).open_pending_group(
        pod_id=pod_id,
        place=place,
        title=title,
        owner_user_id=owner_user_id,
        request_id=request_id,
        answers_outsiders=answers_outsiders,
    )


async def confirm_group(
    uow: SqlAlchemyUnitOfWork,
    *,
    request_id: str,
    external_channel_id: str,
    title: str | None,
    invite_link: str | None,
    surface_ids: Collection[UUID] | None = None,
    platform: str | None = None,
) -> Source | None:
    """The platform made the group the bot asked for. A replay changes nothing."""
    return await SourceRepository(uow.session).confirm(
        request_id=request_id,
        surface_ids=surface_ids,
        platform=platform,
        external_channel_id=external_channel_id,
        title=title,
        invite_link=invite_link,
    )


async def change_place(
    uow: SqlAlchemyUnitOfWork, source_id: UUID, values: Mapping[str, object]
) -> None:
    """Change a shared place's settings (``GROUP_SETTINGS``)."""
    await SourceRepository(uow.session).set(source_id, values)


async def forget_pending_group(
    uow: SqlAlchemyUnitOfWork,
    *,
    request_id: str,
    surface_ids: Collection[UUID] | None = None,
    platform: str | None = None,
) -> None:
    await SourceRepository(uow.session).forget_pending(
        request_id=request_id, surface_ids=surface_ids, platform=platform
    )


async def forget_chat(
    uow: SqlAlchemyUnitOfWork,
    *,
    external_channel_id: str,
    surface_ids: Collection[UUID] | None = None,
    platform: str | None = None,
) -> None:
    """A chat the platform deleted, on these surfaces (or, for ids unique on a
    platform, that platform). Its channel and history stay."""
    await SourceRepository(uow.session).forget_chat(
        external_channel_id=external_channel_id,
        surface_ids=surface_ids,
        platform=platform,
    )
