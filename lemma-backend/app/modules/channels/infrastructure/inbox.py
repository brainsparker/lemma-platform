"""What is new for one member: unread lines, unread mentions, open questions.

Counted by number, not time: a channel's unread is ``last_seq`` past the
member's ``last_read_seq``. Only channels the member is in count lines --
a public group they never joined does not -- and every count is filtered by
what they may see, so a channel they cannot open never shows a badge.
Counts stop at ``UNREAD_CAP``; the app shows "99+" past it.

Unread mentions are counted from the member's own places in the pod -- the
channels they sit in, and those they have a read number in (somebody named
where they are not seated is given one: ``mentions.sync_mentions``) -- each
read as one short range of ``ix_channel_mentions_user`` past their read
number. What they have read is never visited, and a deleted line's mentions
went with it, so no line is read to count them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Subquery,
    Uuid,
    and_,
    column,
    func,
    literal,
    select,
    true,
    union,
    values,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import Channel, NotifyLevel
from app.modules.channels.infrastructure.access import visible_to
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMentionModel,
    ChannelModel,
    ChannelQuestionModel,
    ChannelReadStateModel,
)
from app.modules.channels.infrastructure.questions import still_waiting

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
    #: Whether they are in it.
    joined: bool = False


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
                joined=channel.id in joined,
            )
        return found

    async def _mentions_by_channel(
        self, viewer: Viewer, ids: list[UUID], read: dict[UUID, int]
    ) -> dict[UUID, int]:
        """Each channel's mentions past what the member has read, stopping at
        ``UNREAD_CAP``: one range of ``ix_channel_mentions_user`` per channel,
        so what is read is what is unread -- never every time they were named
        there. (A cap across the page instead, by ``seq``, both read the
        history and lost the counts of channels numbered lower.)"""
        mention = ChannelMentionModel
        page = values(
            column("channel_id", Uuid), column("read_seq", BigInteger), name="page"
        ).data([(channel_id, read.get(channel_id, 0)) for channel_id in ids])
        unread = (
            select(mention.id)
            .where(
                mention.user_id == viewer.user_id,
                mention.pod_id == viewer.pod_id,
                mention.channel_id == page.c.channel_id,
                mention.seq > page.c.read_seq,
            )
            .limit(UNREAD_CAP)
            .lateral("unread")
        )
        rows = await self.session.execute(
            select(page.c.channel_id, func.count(unread.c.id))
            .select_from(page)
            .join(unread, true())
            .group_by(page.c.channel_id)
        )
        return {row[0]: int(row[1]) for row in rows}

    async def _waiting_by_channel(
        self, viewer: Viewer, ids: list[UUID]
    ) -> dict[UUID, int]:
        q = ChannelQuestionModel
        rows = await self.session.execute(
            select(q.channel_id, func.count())
            .where(
                q.awaits_user_id == viewer.user_id,
                q.pod_id == viewer.pod_id,
                q.channel_id.in_(ids),
                still_waiting(),
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
        unread_mentions = await self.session.scalar(
            select(func.count()).select_from(
                self._unread_mentions(viewer, limit=UNREAD_CAP)
            )
        )
        q = ChannelQuestionModel
        waiting = await self.session.scalar(
            select(func.count())
            .select_from(q)
            .join(channel, channel.id == q.channel_id)
            .where(
                q.awaits_user_id == viewer.user_id,
                q.pod_id == viewer.pod_id,
                still_waiting(),
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
        size = max(1, min(limit, UNREAD_CAP))
        newest = self._unread_mentions(viewer, limit=size, newest_first=True)
        rows = await self.session.execute(
            select(newest.c.channel_id, newest.c.message_id)
            .order_by(newest.c.created_at.desc())
            .limit(size)
        )
        return [(row[0], row[1]) for row in rows]

    def _unread_mentions(
        self, viewer: Viewer, *, limit: int, newest_first: bool = False
    ) -> Subquery:
        """Up to ``limit`` unread mentions of the member, per place: each of
        their places, in a channel they may see and not archived, read as one
        range of the mention index past their read number."""
        mention = ChannelMentionModel
        places = _places_of(viewer)
        read = aliased(ChannelReadStateModel, name="read")
        unread = select(
            mention.channel_id, mention.message_id, mention.created_at
        ).where(
            mention.user_id == viewer.user_id,
            mention.pod_id == viewer.pod_id,
            mention.channel_id == places.c.id,
            mention.seq > func.coalesce(read.last_read_seq, literal(0)),
        )
        if newest_first:
            unread = unread.order_by(mention.seq.desc())
        lateral = unread.limit(limit).lateral("unread")
        return (
            select(lateral.c.channel_id, lateral.c.message_id, lateral.c.created_at)
            .select_from(places)
            .join(ChannelModel, ChannelModel.id == places.c.id)
            .outerjoin(
                read,
                and_(read.user_id == viewer.user_id, read.channel_id == places.c.id),
            )
            .join(lateral, true())
            .where(visible_to(viewer), ChannelModel.archived_at.is_(None))
            .limit(limit)
            .subquery("unread_mentions")
        )


def _places_of(viewer: Viewer) -> Subquery:
    """The channels of the pod the member has a place in: a seat, or a read
    number."""
    seat, state = ChannelMemberModel, ChannelReadStateModel
    return union(
        select(seat.channel_id.label("id")).where(
            seat.user_id == viewer.user_id, seat.pod_id == viewer.pod_id
        ),
        select(state.channel_id.label("id")).where(
            state.user_id == viewer.user_id, state.pod_id == viewer.pod_id
        ),
    ).subquery("places")
