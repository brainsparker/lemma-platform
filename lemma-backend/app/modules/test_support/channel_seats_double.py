"""Channel seats kept in memory, the way the channels tables keep them.

A binder asks three things of channels: the source a place is (made, with its
channel, the first time), the conversation a person already has in a channel,
and to put a conversation in one. A double that answered each with a fresh
object would certify a binder that can never find anything it made; this one
keys a source by its place and a channel by whose it is, as the unique indexes
do, so the second message from the same place lands on the same source.

It cannot see conversations another double opened, so a person's conversation
in a channel is found only where a test has seated one (``seat``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.core.authorization.delegation import effective_agent_id
from app.modules.channels.contracts import (
    ContactsHome,
    GroupHome,
    Home,
    MembersHome,
    Place,
    Source,
)

type _PlaceKey = tuple[UUID, str, str, str]
type _SeatKey = tuple[UUID, UUID, UUID]


class InMemoryChannelSeats:
    def __init__(self) -> None:
        self.sources: dict[_PlaceKey, Source] = {}
        self.channels: dict[tuple[object, ...], UUID] = {}
        #: Each conversation's channel, as ``place_in_channel`` left it.
        self.placed: dict[UUID, UUID] = {}
        self._seated: dict[_SeatKey, UUID] = {}

    async def source_for(self, *, pod_id: UUID, place: Place, home: Home) -> Source:
        key = (
            place.surface_id,
            place.platform,
            place.external_channel_id or "",
            place.external_user_id or "",
        )
        found = self.sources.get(key)
        if found is not None:
            return found
        source = Source(
            id=uuid4(),
            channel_id=self._channel(pod_id, home, key),
            pod_id=pod_id,
            surface_id=place.surface_id,
            platform=place.platform,
            kind=place.kind,
            external_channel_id=place.external_channel_id,
            external_user_id=place.external_user_id,
            title=place.title,
            last_inbound_at=datetime.now(timezone.utc),
        )
        self.sources[key] = source
        return source

    async def conversation_in_channel(
        self,
        *,
        channel_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        user_id: UUID,
        contact_id: UUID | None,
    ) -> UUID | None:
        agent = effective_agent_id(agent_id, pod_id=pod_id)
        return self._seated.get((channel_id, agent, contact_id or user_id))

    async def place_in_channel(
        self, *, conversation_id: UUID, channel_id: UUID
    ) -> None:
        self.placed.setdefault(conversation_id, channel_id)

    def seat(
        self,
        conversation_id: UUID,
        *,
        channel_id: UUID,
        pod_id: UUID,
        agent_id: UUID | None,
        person: UUID,
    ) -> None:
        """Give ``person`` -- a member's or a contact's id -- this conversation."""
        agent = effective_agent_id(agent_id, pod_id=pod_id)
        self._seated[(channel_id, agent, person)] = conversation_id
        self.placed[conversation_id] = channel_id

    def _channel(self, pod_id: UUID, home: Home, place: _PlaceKey) -> UUID:
        match home:
            case MembersHome(user_id=user_id):
                key: tuple[object, ...] = ("DIRECT", pod_id, user_id)
            case ContactsHome(contact_id=contact_id):
                key = ("CONTACT", pod_id, contact_id)
            case GroupHome():
                key = ("GROUP", *place)
        return self.channels.setdefault(key, uuid4())
