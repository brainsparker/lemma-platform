"""What is new for one member: unread lines, unread mentions, open questions.

Unread is counted in the channels a member is in -- they spoke there, read
it, were named or asked something there, or it is theirs -- from the later of
when they last read it and when they joined, so a group joined today does not
arrive with its whole history unread. Counts stop at ``UNREAD_CAP``: past it the
app says "99+", and the database does not count a busy group to the end.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, and_, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import InteractionState, Principal
from app.modules.channels.infrastructure.models import (
    ChannelMentionModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelParticipantModel,
)
from app.modules.channels.infrastructure.rows import MAX_LINES, now

#: Where unread counting stops; the app shows "99+" past it.
UNREAD_CAP = 100


@dataclass(frozen=True, slots=True)
class Unread:
    """What is new in one channel for one member."""

    lines: int = 0
    mentions: int = 0
    waiting: int = 0


@dataclass(frozen=True, slots=True)
class Totals:
    """What is new for one member across a pod: the app's badges."""

    channels: int
    mentions: int
    waiting: int


class InboxReads:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def unread(
        self, *, user_id: UUID, channel_ids: Sequence[UUID]
    ) -> dict[UUID, Unread]:
        """Per channel: new lines, unread mentions and open questions for them."""
        if not channel_ids:
            return {}
        ids = list(channel_ids)[:MAX_LINES]
        lines = await self._unread_lines(user_id=user_id, channel_ids=ids)
        mentions = await self._count_by_channel(
            select(ChannelMentionModel.channel_id, func.count())
            .where(
                ChannelMentionModel.user_id == user_id,
                ChannelMentionModel.read_at.is_(None),
                ChannelMentionModel.channel_id.in_(ids),
            )
            .group_by(ChannelMentionModel.channel_id)
        )
        waiting = await self._count_by_channel(
            select(ChannelMessageModel.channel_id, func.count())
            .where(
                ChannelMessageModel.awaits_user_id == user_id,
                ChannelMessageModel.interaction_state == InteractionState.OPEN.value,
                ChannelMessageModel.channel_id.in_(ids),
            )
            .group_by(ChannelMessageModel.channel_id)
        )
        return {
            channel_id: Unread(
                lines=lines.get(channel_id, 0),
                mentions=mentions.get(channel_id, 0),
                waiting=waiting.get(channel_id, 0),
            )
            for channel_id in ids
        }

    async def _unread_lines(
        self, *, user_id: UUID, channel_ids: list[UUID]
    ) -> dict[UUID, int]:
        participant = ChannelParticipantModel
        # After the last read; or, never read, from joining -- inclusive, since
        # being named joins one in the same moment as the line naming them.
        since = or_(
            and_(
                participant.last_read_at.is_not(None),
                ChannelMessageModel.created_at > participant.last_read_at,
            ),
            and_(
                participant.last_read_at.is_(None),
                ChannelMessageModel.created_at >= participant.created_at,
            ),
        )
        newer = (
            select(literal(1))
            .where(
                ChannelMessageModel.channel_id == participant.channel_id,
                since,
                ChannelMessageModel.thread_root_id.is_(None),
                ChannelMessageModel.deleted_at.is_(None),
                ChannelMessageModel.sender_user_id.is_distinct_from(user_id),
            )
            .limit(UNREAD_CAP)
            .correlate(participant)
            .subquery()
        )
        counted = select(func.count()).select_from(newer).scalar_subquery()
        rows = await self.session.execute(
            select(participant.channel_id, counted).where(
                participant.user_id == user_id,
                participant.channel_id.in_(channel_ids),
            )
        )
        return {channel_id: int(count) for channel_id, count in rows.tuples()}

    async def _count_by_channel(
        self, statement: Select[tuple[UUID, int]]
    ) -> dict[UUID, int]:
        rows = await self.session.execute(statement)
        return {channel_id: int(count) for channel_id, count in rows.tuples()}

    async def totals(self, *, user_id: UUID, pod_id: UUID) -> Totals:
        """Across the pod: channels with something unread, unread mentions,
        and questions waiting on them."""
        mine = select(ChannelParticipantModel.channel_id).where(
            ChannelParticipantModel.user_id == user_id,
            ChannelParticipantModel.pod_id == pod_id,
        )
        live = select(ChannelModel.id).where(
            ChannelModel.id.in_(mine), ChannelModel.archived_at.is_(None)
        )
        ids = list(await self.session.scalars(live.limit(MAX_LINES)))
        unread = await self._unread_lines(user_id=user_id, channel_ids=ids)
        mentions = await self.session.scalar(
            select(func.count())
            .select_from(ChannelMentionModel)
            .where(
                ChannelMentionModel.user_id == user_id,
                ChannelMentionModel.pod_id == pod_id,
                ChannelMentionModel.read_at.is_(None),
            )
        )
        waiting = await self.session.scalar(
            select(func.count())
            .select_from(ChannelMessageModel)
            .where(
                ChannelMessageModel.awaits_user_id == user_id,
                ChannelMessageModel.pod_id == pod_id,
                ChannelMessageModel.interaction_state == InteractionState.OPEN.value,
            )
        )
        return Totals(
            channels=sum(1 for count in unread.values() if count),
            mentions=int(mentions or 0),
            waiting=int(waiting or 0),
        )

    # -- mentions -------------------------------------------------------------

    async def mention(
        self,
        *,
        message_id: UUID,
        channel_id: UUID,
        pod_id: UUID,
        people: Sequence[Principal] = (),
        agent_ids: Sequence[UUID] = (),
    ) -> None:
        """Record who a line names; naming somebody twice records them once."""
        rows = [
            {
                "message_id": message_id,
                "channel_id": channel_id,
                "pod_id": pod_id,
                "user_id": person.user_id,
                "contact_id": person.contact_id,
            }
            for person in people
        ] + [
            {
                "message_id": message_id,
                "channel_id": channel_id,
                "pod_id": pod_id,
                "agent_id": agent_id,
            }
            for agent_id in agent_ids
        ]
        if rows:
            await self.session.execute(
                insert(ChannelMentionModel).values(rows).on_conflict_do_nothing()
            )

    async def mentioned_in(self, message_id: UUID) -> list[Principal | UUID]:
        """Who a line names: people as principals, agents by id."""
        rows = await self.session.execute(
            select(
                ChannelMentionModel.user_id,
                ChannelMentionModel.contact_id,
                ChannelMentionModel.agent_id,
            )
            .where(ChannelMentionModel.message_id == message_id)
            .limit(MAX_LINES)
        )
        named: list[Principal | UUID] = []
        for user_id, contact_id, agent_id in rows.tuples():
            if agent_id is not None:
                named.append(agent_id)
            else:
                named.append(Principal(user_id=user_id, contact_id=contact_id))
        return named

    async def read_mentions(self, *, channel_id: UUID, user_id: UUID) -> None:
        await self.session.execute(
            update(ChannelMentionModel)
            .where(
                ChannelMentionModel.channel_id == channel_id,
                ChannelMentionModel.user_id == user_id,
                ChannelMentionModel.read_at.is_(None),
            )
            .values(read_at=now())
        )
