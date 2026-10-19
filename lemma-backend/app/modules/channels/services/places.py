"""Which channel an outside place belongs to, decided the first time it is heard.

A place is in an external account; the account is made the first time any of
its places is heard, and keeps them across a reinstalled bot. A private chat
with a member is a source of that member's DIRECT channel, a contact's of
theirs, a group of a GROUP channel of its own -- private when the platform
keeps it to its members, public otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import (
    Channel,
    Principal,
    Source,
    SourceKind,
    Visibility,
)
from app.modules.channels.infrastructure.accounts import AccountRepository
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.source_repository import SourceRepository


class _LostTheRace(Exception):
    """Another writer added the group's source first; this channel goes back."""


@dataclass(frozen=True, slots=True)
class MembersHome:
    """A member's private chat: their DIRECT channel."""

    user_id: UUID


@dataclass(frozen=True, slots=True)
class ContactsHome:
    """A contact's private chat, web chat or email: their CONTACT channel."""

    contact_id: UUID


@dataclass(frozen=True, slots=True)
class GroupHome:
    """A chat several people read: a GROUP channel of its own. ``owner``: the
    member who brought the bot in, who is in it from the start."""

    title: str | None
    owner_user_id: UUID | None = None


type Home = MembersHome | ContactsHome | GroupHome


@dataclass(frozen=True, slots=True)
class Place:
    """An outside place, as the surface that hears it names it.

    ``account_key`` is the account's own name on its platform -- a Slack team,
    a Teams tenant, a WhatsApp number, a bot, an inbox, a web widget -- and
    ``surface_id`` the installation that reaches it now (``None`` for a web
    widget, which no surface carries).
    """

    surface_id: UUID | None
    platform: str
    account_key: str
    kind: SourceKind
    external_channel_id: str | None
    external_user_id: str | None
    title: str | None = None
    #: The platform keeps it to its members.
    is_private: bool = True


class ChannelPlaces:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.session = uow.session
        self.channels = ChannelRepository(uow.session)
        self.sources = SourceRepository(uow.session)
        self.accounts = AccountRepository(uow.session)
        self.members = MemberRepository(uow.session)

    async def source_for(self, *, pod_id: UUID, place: Place, home: Home) -> Source:
        """The source for ``place``, made -- with its channel -- the first time."""
        account = await self.accounts.account_for(
            pod_id=pod_id,
            platform=place.platform,
            account_key=place.account_key,
            surface_id=place.surface_id,
        )
        found = await self._find(account.id, place)
        if found is not None:
            return found
        if isinstance(home, GroupHome):
            return await self._group_source(pod_id, account.id, place, home)
        channel = await self._home(pod_id, home)
        added = await self._add(channel, account.id, place)
        return added or await self._winner(account.id, place)

    async def _group_source(
        self, pod_id: UUID, account_id: UUID, place: Place, home: GroupHome
    ) -> Source:
        """A group's channel and its source, made together or not at all."""
        try:
            async with self.session.begin_nested():
                channel = await self.channels.open_group(
                    pod_id=pod_id,
                    title=home.title or place.title,
                    visibility=(
                        Visibility.PRIVATE if place.is_private else Visibility.PUBLIC
                    ),
                    created_by_user_id=home.owner_user_id,
                )
                added = await self._add(channel, account_id, place)
                if added is None:
                    raise _LostTheRace
                return added
        except IntegrityError, _LostTheRace:
            return await self._winner(account_id, place)

    async def open_pending_group(
        self,
        *,
        pod_id: UUID,
        place: Place,
        title: str,
        owner_user_id: UUID,
        request_id: str,
        answers_outsiders: bool,
    ) -> Source:
        """A group the bot asked a platform to create: its private channel,
        with its owner in it, and a place with no id until confirmed."""
        account = await self.accounts.account_for(
            pod_id=pod_id,
            platform=place.platform,
            account_key=place.account_key,
            surface_id=place.surface_id,
        )
        channel = await self.channels.open_group(
            pod_id=pod_id,
            title=title,
            visibility=Visibility.PRIVATE,
            created_by_user_id=owner_user_id,
        )
        return await self.sources.add_pending(
            channel=channel,
            account_id=account.id,
            platform=place.platform,
            title=title,
            owner_user_id=owner_user_id,
            request_id=request_id,
            answers_outsiders=answers_outsiders,
        )

    async def _home(self, pod_id: UUID, home: MembersHome | ContactsHome) -> Channel:
        if isinstance(home, MembersHome):
            return await self.channels.direct(pod_id=pod_id, user_id=home.user_id)
        return await self.channels.for_contact(
            pod_id=pod_id, contact_id=home.contact_id
        )

    async def _add(
        self, channel: Channel, account_id: UUID, place: Place
    ) -> Source | None:
        return await self.sources.add(
            channel=channel,
            account_id=account_id,
            platform=place.platform,
            kind=place.kind,
            is_private=place.is_private,
            external_channel_id=place.external_channel_id,
            external_user_id=place.external_user_id,
            title=place.title,
        )

    async def _find(self, account_id: UUID, place: Place) -> Source | None:
        return await self.sources.find(
            account_id=account_id,
            external_channel_id=place.external_channel_id,
            external_user_id=place.external_user_id,
        )

    async def _winner(self, account_id: UUID, place: Place) -> Source:
        winner = await self._find(account_id, place)
        if winner is None:  # pragma: no cover - the conflict named it
            raise LookupError("A source another writer added vanished")
        return winner

    async def join(self, channel: Channel, principal: Principal) -> None:
        """Somebody the platform shows is in a place is in its channel."""
        await self.members.add_person(channel, principal)
