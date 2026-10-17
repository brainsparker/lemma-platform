"""Who has spoken in channels, and where a shared place's source is.

Two reads behind a list of groups, each asked once for all of them: everybody
who said something in each channel, and the source each of the pod's groups
is. A page of a few hundred groups is then two statements, not a few per group.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import (
    Principal,
    SenderKind,
    Source,
    SourceKind,
)
from app.modules.channels.infrastructure.models import (
    ChannelMessageModel,
    ChannelSourceModel,
)
from app.modules.channels.infrastructure.repository import source_entity

#: Ceilings on one read. A pod lists at most a few hundred groups.
MAX_SPEAKERS = 5000
_MAX_SOURCES = 2000


class Speaker(BaseModel):
    """Somebody who said something in a channel: a member or a contact."""

    model_config = ConfigDict(frozen=True)

    channel_id: UUID
    principal: Principal
    #: The name they last spoke under, as the platform showed it.
    name: str | None
    last_said_at: datetime


class SpeakerReads:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def speakers(
        self, channel_ids: Collection[UUID], *, limit: int = MAX_SPEAKERS
    ) -> list[Speaker]:
        """The people who spoke in these channels, once per channel, newest first.

        Agents are not speakers: an agent is attached to a channel, never one
        of the people in it.
        """
        if not channel_ids:
            return []
        message = ChannelMessageModel
        last_said_at = func.max(message.created_at)
        rows = (
            await self.session.execute(
                select(
                    message.channel_id,
                    message.sender_user_id,
                    message.sender_contact_id,
                    func.max(message.sender_name),
                    last_said_at,
                )
                .where(
                    message.channel_id.in_(sorted(channel_ids)),
                    message.sender_kind != SenderKind.AGENT.value,
                    (message.sender_user_id.is_not(None))
                    | (message.sender_contact_id.is_not(None)),
                )
                .group_by(
                    message.channel_id,
                    message.sender_user_id,
                    message.sender_contact_id,
                )
                .order_by(last_said_at.desc())
                .limit(max(1, min(limit, MAX_SPEAKERS)))
            )
        ).all()
        return [
            Speaker(
                channel_id=channel_id,
                principal=(
                    Principal.member(user_id)
                    if user_id is not None
                    else Principal.contact(contact_id)
                ),
                name=name,
                last_said_at=said_at,
            )
            for channel_id, user_id, contact_id, name, said_at in rows
        ]

    async def shared_sources(
        self, places: Collection[tuple[UUID, str]]
    ) -> dict[tuple[UUID, str], Source]:
        """The shared source at each ``(surface, chat)``, where there is one yet."""
        if not places:
            return {}
        source = ChannelSourceModel
        rows = await self.session.scalars(
            select(source)
            .where(
                source.kind == SourceKind.SHARED.value,
                source.external_user_id.is_(None),
                source.surface_id.in_(sorted({surface for surface, _ in places})),
                source.external_channel_id.in_(sorted({chat for _, chat in places})),
            )
            .limit(_MAX_SOURCES)
        )
        found = {
            (row.surface_id, row.external_channel_id): source_entity(row)
            for row in rows
            if row.surface_id is not None and row.external_channel_id is not None
        }
        return {place: found[place] for place in places if place in found}
