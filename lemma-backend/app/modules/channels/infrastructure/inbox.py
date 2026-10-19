"""What is new for one member: unread lines, unread mentions, open questions.

Counted by number, not time: a channel's unread is ``last_seq`` past the
member's ``last_read_seq``. Only channels the member is in count lines --
a public group they never joined does not -- and every count is filtered by
what they may see, so a channel they cannot open never shows a badge.
Counts stop at ``UNREAD_CAP``; the app shows "99+" past it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import Channel, NotifyLevel, QuestionState
from app.modules.channels.infrastructure.access import visible_to
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMentionModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
    ChannelReadStateModel,
)

#: Where unread counting stops; the app shows "99+" past it.
UNREAD_CAP = 100


@dataclass(frozen=True, slots=True)
class Unread:
    """What is new in one channel for one member."""

    lines: int = 0
    mentions: int = 0
    waiting: int = 0
    #: How far they have read.
    read_seq: int = 0


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
        self, viewer: Viewer, channels: Sequence[Channel]
    ) -> dict[UUID, Unread]:
        """Per channel: new lines, unread mentions and open questions."""
        if not channels:
            return {}
        ids = [channel.id for channel in channels]
        joined = set(
            await self.session.scalars(
                select(ChannelMemberModel.channel_id)
                .where(
                    ChannelMemberModel.channel_id.in_(ids),
                    ChannelMemberModel.user_id == viewer.user_id,
                )
                .limit(len(ids))
            )
        )
        rows = await self.session.execute(
            select(
                ChannelReadStateModel.channel_id,
                ChannelReadStateModel.last_read_seq,
            ).where(
                ChannelReadStateModel.user_id == viewer.user_id,
                ChannelReadStateModel.channel_id.in_(ids),
            )
        )
        read: dict[UUID, int] = dict(rows.tuples().all())
        mentions = await self._mentions_by_channel(viewer, ids, read)
        waiting = await self._waiting_by_channel(viewer, ids)
        found: dict[UUID, Unread] = {}
        for channel in channels:
            upto = read.get(channel.id, 0)
            lines = (
                min(UNREAD_CAP, max(0, channel.last_seq - upto))
                if channel.id in joined
                else 0
            )
            found[channel.id] = Unread(
                lines=lines,
                mentions=mentions.get(channel.id, 0),
                waiting=waiting.get(channel.id, 0),
                read_seq=upto,
            )
        return found

    async def _mentions_by_channel(
        self, viewer: Viewer, ids: list[UUID], read: dict[UUID, int]
    ) -> dict[UUID, int]:
        mention = ChannelMentionModel
        rows = await self.session.execute(
            select(mention.channel_id, mention.seq)
            .join(ChannelMessageModel, ChannelMessageModel.id == mention.message_id)
            .where(
                mention.user_id == viewer.user_id,
                mention.channel_id.in_(ids),
                ChannelMessageModel.deleted_at.is_(None),
            )
            .order_by(mention.seq.desc())
            .limit(UNREAD_CAP * 10)
        )
        counts: dict[UUID, int] = {}
        for channel_id, seq in rows:
            if seq > int(read.get(channel_id, 0)):
                counts[channel_id] = min(UNREAD_CAP, counts.get(channel_id, 0) + 1)
        return counts

    async def _waiting_by_channel(
        self, viewer: Viewer, ids: list[UUID]
    ) -> dict[UUID, int]:
        q = ChannelQuestionModel
        rows = await self.session.execute(
            select(q.channel_id, func.count())
            .where(
                q.awaits_user_id == viewer.user_id,
                q.channel_id.in_(ids),
                q.state == QuestionState.OPEN.value,
            )
            .group_by(q.channel_id)
        )
        return {row[0]: min(UNREAD_CAP, int(row[1])) for row in rows}

    async def totals(self, viewer: Viewer) -> Totals:
        """Badges across the pod: channels with something new (that the
        member hears of), unread mentions, questions waiting on them."""
        channel = ChannelModel
        member = ChannelMemberModel
        state = ChannelReadStateModel
        new_channels = await self.session.scalar(
            select(func.count())
            .select_from(channel)
            .join(
                member,
                and_(member.channel_id == channel.id, member.user_id == viewer.user_id),
            )
            .outerjoin(
                state,
                and_(state.channel_id == channel.id, state.user_id == viewer.user_id),
            )
            .where(
                visible_to(viewer),
                channel.archived_at.is_(None),
                member.notify != NotifyLevel.NONE.value,
                channel.last_seq > func.coalesce(state.last_read_seq, 0),
            )
        )
        mention = ChannelMentionModel
        unread_mentions = await self.session.scalar(
            select(func.count())
            .select_from(mention)
            .join(channel, channel.id == mention.channel_id)
            .join(ChannelMessageModel, ChannelMessageModel.id == mention.message_id)
            .outerjoin(
                state,
                and_(
                    state.channel_id == mention.channel_id,
                    state.user_id == viewer.user_id,
                ),
            )
            .where(
                mention.user_id == viewer.user_id,
                visible_to(viewer),
                channel.archived_at.is_(None),
                ChannelMessageModel.deleted_at.is_(None),
                mention.seq > func.coalesce(state.last_read_seq, 0),
            )
        )
        q = ChannelQuestionModel
        waiting = await self.session.scalar(
            select(func.count())
            .select_from(q)
            .join(channel, channel.id == q.channel_id)
            .where(
                q.awaits_user_id == viewer.user_id,
                q.state == QuestionState.OPEN.value,
                visible_to(viewer),
            )
        )
        return Totals(
            channels=int(new_channels or 0),
            mentions=min(UNREAD_CAP, int(unread_mentions or 0)),
            waiting=min(UNREAD_CAP, int(waiting or 0)),
        )

    async def mentioned_in(
        self, viewer: Viewer, *, limit: int
    ) -> list[tuple[UUID, UUID]]:
        """Unread mentions of the member, newest first: (channel, line)."""
        mention = ChannelMentionModel
        state = ChannelReadStateModel
        rows = await self.session.execute(
            select(mention.channel_id, mention.message_id)
            .join(ChannelModel, ChannelModel.id == mention.channel_id)
            .join(ChannelMessageModel, ChannelMessageModel.id == mention.message_id)
            .outerjoin(
                state,
                and_(
                    state.channel_id == mention.channel_id,
                    state.user_id == viewer.user_id,
                ),
            )
            .where(
                mention.user_id == viewer.user_id,
                visible_to(viewer),
                ChannelModel.archived_at.is_(None),
                ChannelMessageModel.deleted_at.is_(None),
                mention.seq > func.coalesce(state.last_read_seq, 0),
            )
            .order_by(mention.created_at.desc())
            .limit(max(1, min(limit, UNREAD_CAP)))
        )
        return [(row[0], row[1]) for row in rows]
