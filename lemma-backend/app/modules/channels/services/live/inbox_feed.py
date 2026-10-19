"""The ``inbox`` topic's half of a pod's hub: whose counts moved, and to what.

The inbox is not a log to replay; it is counts, worked out again from the
tables whenever something they count may have moved -- a channel's change
counter (a line, a question, a seat) or a reader's read position. The feed
remembers every channel's head as it last saw it, so on a signal it reads only
the channels named, and on a poll (or after signals may have been missed)
every channel of the pod, in one query, to find the ones that moved.

Each event is absolute -- the channel's counts and the totals now -- so a
client applies whichever came last; a subscriber that resumes is told to
refetch ``/inbox`` instead (``resync``).

A reader's counts are worked out together for every channel that moved for
them: a fixed number of reads per reader however many channels moved. The
feed also remembers which channels each reader may see, so one that stops
being theirs -- taken out of a private group, the grant gone, the channel
deleted -- is sent once more with nothing in it, and its badge clears; a
channel they never saw is never named to them.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.live.protocol import ResyncReason, UnsubscribedReason
from sqlalchemy import select

from app.modules.channels.api.access import Caller
from app.modules.channels.domain.access import may_see
from app.modules.channels.domain.entities import Channel
from app.modules.channels.infrastructure.access import visible_to
from app.modules.channels.infrastructure.inbox import InboxReads
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.models import ChannelModel
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.services.live.events import (
    Head,
    LinesView,
    heads_of,
    inbox_updated,
    lines_view,
    pod_heads,
    question_waiting,
)
from app.modules.channels.services.live.readers import caller_for
from app.modules.channels.services.live.subscriptions import InboxSub

#: Lines one channel's change is searched for new questions in.
_QUESTION_SCAN = 50
#: The channels a reader is taken to have seen when they subscribe: the most
#: recently active this many. One quieter than all of them that they then lose
#: sight of is not cleared over the socket; their next ``/inbox`` read clears it.
SEEN_AT_JOIN = 2000


class InboxFeed:
    def __init__(self, pod_id: UUID) -> None:
        self.pod_id = pod_id
        #: Subscriptions by reader.
        self.subs: dict[UUID, set[InboxSub]] = {}
        #: Every channel of the pod as last read; ``None`` before the first.
        self._heads: dict[UUID, Head] | None = None
        #: memory: per reader, the pod's channels they may see -- from
        #: ``SEEN_AT_JOIN`` at first, plus those sent to them since; dropped
        #: with the reader's last subscription.
        self._seen: dict[UUID, set[UUID]] = {}

    def __bool__(self) -> bool:
        return bool(self.subs)

    @property
    def users(self) -> set[UUID]:
        return set(self.subs)

    async def join(self, uow: SqlAlchemyUnitOfWork, sub: InboxSub) -> None:
        """Index ``sub``. The first reader reads every head, so the next change
        is measured from now. Runs under the hub's lock."""
        if self._heads is None:
            self._heads = await pod_heads(uow, self.pod_id)
        if sub.user_id not in self._seen:
            self._seen[sub.user_id] = await _seen_by(uow, sub.caller)
        self.subs.setdefault(sub.user_id, set()).add(sub)

    def release(self, sub: InboxSub) -> None:
        subs = self.subs.get(sub.user_id)
        if subs is None:
            return
        subs.discard(sub)
        if not subs:
            self.subs.pop(sub.user_id, None)
            self._seen.pop(sub.user_id, None)
        if not self.subs:
            self._heads = None

    async def drain(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        dirty: Collection[UUID],
        reads: Collection[tuple[UUID, UUID]],
        everything: bool,
    ) -> None:
        """Tell each reader about the channels whose counts may have moved for
        them. ``everything``: compare every channel of the pod, not only
        ``dirty``."""
        if not self.subs or self._heads is None:
            return
        moved = await self._moved(uow, dirty, everything=everything)
        for_user: dict[UUID, set[UUID]] = {}
        for channel_id in moved:
            for user_id in self.subs:
                for_user.setdefault(user_id, set()).add(channel_id)
        for channel_id, user_id in reads:
            if user_id in self.subs:
                for_user.setdefault(user_id, set()).add(channel_id)
        if not for_user:
            return
        wanted = {channel_id for ids in for_user.values() for channel_id in ids}
        channels = await ChannelRepository(uow.session).by_ids(wanted)
        views: dict[UUID, LinesView] = {}
        for channel_id, (before, _after) in moved.items():
            channel = channels.get(channel_id)
            if channel is not None and _after.seq > before.seq:
                lines = await LineRepository(uow.session).changed_since(
                    channel_id, after_rev=before.rev, limit=_QUESTION_SCAN
                )
                views[channel_id] = await lines_view(
                    uow, channel, lines, since_seq=before.seq
                )
        for user_id, channel_ids in for_user.items():
            await self._tell(uow, user_id, channel_ids, channels, views)

    async def _moved(
        self, uow: SqlAlchemyUnitOfWork, dirty: Collection[UUID], *, everything: bool
    ) -> dict[UUID, tuple[Head, Head]]:
        assert self._heads is not None
        now = (
            await pod_heads(uow, self.pod_id)
            if everything
            else await heads_of(uow, dirty)
        )
        moved: dict[UUID, tuple[Head, Head]] = {}
        for channel_id, head in now.items():
            before = self._heads.get(channel_id, Head(0, 0))
            if head.rev > before.rev:
                moved[channel_id] = (before, head)
            self._heads[channel_id] = head
        # A channel deleted: its readers' badges clear.
        looked_at = set(self._heads) if everything else set(dirty)
        for gone in looked_at - set(now):
            before = self._heads.pop(gone, None)
            if before is not None:
                moved[gone] = (before, before)
        return moved

    async def _tell(
        self,
        uow: SqlAlchemyUnitOfWork,
        user_id: UUID,
        channel_ids: set[UUID],
        channels: dict[UUID, Channel],
        views: dict[UUID, LinesView],
    ) -> None:
        subs = [sub for sub in self.subs.get(user_id, ()) if sub.active]
        if not subs:
            return
        caller = subs[0].caller
        seen = self._seen.setdefault(user_id, set())
        joined = await MemberRepository(uow.session).channels_of(
            user_id,
            [channel_id for channel_id in channel_ids if channel_id in channels],
        )
        shown: list[Channel] = []
        lost: list[UUID] = []
        for channel_id in sorted(channel_ids):
            channel = channels.get(channel_id)
            if channel is not None and may_see(
                channel, caller.viewer, is_member=channel_id in joined
            ):
                shown.append(channel)
                seen.add(channel_id)
            elif channel_id in seen:
                lost.append(channel_id)
                seen.discard(channel_id)
        if not shown and not lost:
            return
        inbox = InboxReads(uow.session)
        unread = await inbox.unread(caller.viewer, shown)
        totals = await inbox.totals(caller.viewer)
        events: list[str] = []
        for channel in shown:
            events.append(inbox_updated(channel.id, unread.get(channel.id), totals))
            view = views.get(channel.id)
            if view is not None:
                events.extend(question_waiting(view, user_id))
        # What they can no longer see counts for nothing: the badge clears.
        events.extend(inbox_updated(channel_id, None, totals) for channel_id in lost)
        for sub in subs:
            sub.connection.offer_events(sub.id, None, events)

    async def recheck(
        self, uow: SqlAlchemyUnitOfWork, end: Callable[[InboxSub, str], None]
    ) -> None:
        """The pod's grants moved: rebuild each reader, end those who may no
        longer read channels, and have the rest refetch their counts."""
        for user_id, subs in list(self.subs.items()):
            caller = await caller_for(uow, pod_id=self.pod_id, user_id=user_id)
            if caller is not None:
                self._seen[user_id] = await _seen_by(uow, caller)
            for sub in list(subs):
                if caller is None:
                    end(sub, UnsubscribedReason.ACCESS_REVOKED.value)
                    continue
                sub.caller = caller
                sub.connection.offer_resync(
                    sub.id, None, ResyncReason.PERMISSION_CHANGED
                )


async def _seen_by(uow: SqlAlchemyUnitOfWork, caller: Caller) -> set[UUID]:
    """The pod's channels the reader may see now -- the most recently active
    ``SEEN_AT_JOIN`` of them; any other is added once it is sent to them."""
    rows = await uow.session.scalars(
        select(ChannelModel.id)
        .where(visible_to(caller.viewer))
        .order_by(ChannelModel.last_line_at.desc())
        .limit(SEEN_AT_JOIN)
    )
    return set(rows)


__all__ = ["InboxFeed"]
