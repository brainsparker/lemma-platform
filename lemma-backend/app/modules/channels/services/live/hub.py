"""The per-process channels hub: one reader of a pod's channels for every socket.

Each API process keeps at most one hub per pod that some ``channel`` or
``inbox`` subscription in it watches. The hub hears the pod's signals
(``app/core/live/signals.py``): ``channel`` (a channel's change counter
moved), ``read`` (a member's read position moved), and the ephemeral
``typing`` and ``answering``. It reads what moved once per batch -- the
channel's head, the lines changed since the hub's own cursor for it, the
names they need -- and projects each line once per kind of reader, so database
work grows with watched channels and their changes, never with sockets.

Nothing is lost to a missed signal. A channel's changes are numbered under its
row lock (``infrastructure.revisions``) and the hub reads ``rev > its own``;
an idle poll re-reads every watched head, and read positions changed since the
last poll.

A subscription joins at the hub's cursor for its channel. One that resumes
(``since``) is first sent its own gap, read directly under the hub's lock so
the live fan-out cannot interleave with it. A line may then reach it twice --
in its gap and again in the next batch -- which is harmless: every event
carries the whole line, and clients apply events as upserts by id.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.live.config import LiveSettings
from app.core.live.connection import LiveConnection
from app.core.live.protocol import (
    LiveAccessDenied,
    LiveErrorCode,
    ResyncReason,
    UnsubscribedReason,
)
from app.core.live.signals import Signal, SignalListeners
from app.core.live.topics import LiveRegistry
from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.modules.channels.api.access import Caller
from app.modules.channels.domain.access import may_see
from app.modules.channels.domain.entities import Channel
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.models import ChannelReadStateModel
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.services.live.events import (
    Head,
    channel_updated,
    heads_of,
    lines_view,
    parse_cursor,
    read_updated,
    typing_event,
)
from app.modules.channels.services.live.inbox_feed import InboxFeed
from app.modules.channels.services.live.readers import caller_for
from app.modules.channels.services.live.answers import (
    Answers,
    answering_for,
    answering_of,
    as_uuid,
)
from app.modules.channels.services.live.instruments import (
    backoff,
    hub_drain_seconds,
    hub_reads,
    hub_restarts,
    hub_resyncs,
)
from app.modules.channels.services.live.subscriptions import ChannelSub, InboxSub

logger = get_logger(__name__)
EpochReader = Callable[[UUID], Awaitable[str | None]]

#: The signals the hub hears.
SIGNAL_KINDS = ["channel", "read", "typing", "answering"]
#: How far back a poll looks for read positions, past the last one: a read
#: committed just after a poll began is still found by the next.
_READ_OVERLAP = timedelta(seconds=5)


class ChannelsHub:
    def __init__(
        self,
        pod_id: UUID,
        *,
        uow_factory: UnitOfWorkFactory,
        listeners: SignalListeners,
        streams: LiveRegistry,
        epoch_of: EpochReader,
        settings: LiveSettings,
        on_idle: Callable[["ChannelsHub"], None],
        backoff: Callable[[int], float] = backoff,
    ) -> None:
        self.pod_id = pod_id
        self._uow_factory = uow_factory
        self._listeners = listeners
        self._epoch_of = epoch_of
        self._settings = settings
        self._on_idle = on_idle
        self._backoff = backoff
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._channels: dict[UUID, set[ChannelSub]] = {}
        #: The hub's cursor per watched channel.
        self._heads: dict[UUID, Head] = {}
        self.inbox = InboxFeed(pod_id)
        self._dirty: set[UUID] = set()
        self._reads: set[tuple[UUID, UUID]] = set()
        self._everything = False
        self._joining = 0
        self._task: asyncio.Task[None] | None = None
        #: Unforeseen ends of the loop since it last read cleanly.
        self._loop_failures = 0
        self._started = False
        self._epoch: str | None = None
        self._next_epoch_check = 0.0
        self._next_poll = 0.0
        self._last_read = 0.0
        self._reads_since: datetime | None = None
        self.idle_since: float | None = None
        self._answers = Answers(
            deliver=self._deliver_to,
            streams=streams,
            longest=settings.live_answer_stream_seconds,
        )

    # -- lifecycle ------------------------------------------------------------

    async def _ensure_started(self) -> None:
        # Claimed before the first await, so two joins racing to start the
        # hub cannot both register for its signals.
        if self._started:
            if self._task is None or self._task.done():
                # Its restart was lost (it is scheduled from the loop's end):
                # the next subscriber starts it again.
                self._start_loop()
            return
        self._started = True
        listener = await self._listeners.of(self.pod_id)
        listener.listen(SIGNAL_KINDS, self.on_signal, self.catch_up)
        self._epoch = await self._epoch_of(self.pod_id)
        now = time.monotonic()
        self._next_epoch_check = now + self._settings.live_authz_check_seconds
        self._next_poll = now + self._settings.live_idle_poll_seconds
        self._start_loop()

    def _start_loop(self, *, after: float = 0.0) -> None:
        self._task = create_background_task(
            self._run(after=after), name=f"live-channels-{self.pod_id}"
        )
        self._task.add_done_callback(self._loop_ended)

    def _loop_ended(self, task: asyncio.Task[None]) -> None:
        """The loop met an error nobody foresaw -- a bug, not an outage. It is
        started again after a backoff rather than leave every socket of the
        pod in this process silent until a new subscriber happens to come; the
        backoff keeps a fault that repeats from spinning."""
        if task.cancelled() or not self._started or task is not self._task:
            return
        fault = task.exception()
        if fault is None:
            return
        self._loop_failures += 1
        hub_restarts.add(1)
        logger.error(
            "channels.live.hub_loop_failed",
            pod_id=str(self.pod_id),
            error_type=type(fault).__name__,
            failures=self._loop_failures,
            exc_info=fault,
        )
        self._start_loop(after=self._backoff(self._loop_failures))

    async def stop(self) -> None:
        self._started = False
        task, self._task = self._task, None
        listener = await self._listeners.of(self.pod_id)
        listener.forget(self.on_signal, self.catch_up)
        await self._listeners.release(self.pod_id)
        await self._answers.close()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def subscription_count(self) -> int:
        return sum(len(subs) for subs in self._channels.values()) + sum(
            len(subs) for subs in self.inbox.subs.values()
        )

    def _maybe_idle(self) -> None:
        if (
            not self._channels
            and not self.inbox
            and not self._joining
            and self.idle_since is None
        ):
            self.idle_since = time.monotonic()
            self._on_idle(self)

    # -- joining ----------------------------------------------------------------

    async def join_channel(
        self,
        connection: LiveConnection,
        subscription_id: str,
        *,
        caller: Caller,
        channel: Channel,
        since: str | None,
    ) -> None:
        """Index a ``channel`` subscription, sending its gap since ``since``."""
        self._joining += 1
        self.idle_since = None
        try:
            await self._ensure_started()
            async with self._lock, self._uow_factory() as uow:
                if connection.closed:
                    return
                head = self._heads.get(channel.id)
                if head is None:
                    head = (await heads_of(uow, [channel.id])).get(channel.id)
                    if head is None:
                        raise LiveAccessDenied(
                            LiveErrorCode.NOT_FOUND, "Channel not found"
                        )
                    self._heads[channel.id] = head
                sub = ChannelSub(
                    id=subscription_id,
                    connection=connection,
                    channel_id=channel.id,
                    caller=caller,
                    cursor=head.cursor,
                    on_release=self._release_channel,
                )
                connection.subscriptions[subscription_id] = sub
                connection.offer_subscribed(subscription_id, head.cursor)
                await self._send_gap(uow, sub, channel, head, since)
                if sub.active:
                    # Nothing awaited from here: a delta relayed now reaches
                    # it after the snapshot, never before.
                    self._channels.setdefault(channel.id, set()).add(sub)
                    caught_up = self._answers.so_far(channel.id, sub.user_id)
                    if caught_up:
                        connection.offer_events(sub.id, sub.cursor, caught_up)
        finally:
            self._joining -= 1
            self._maybe_idle()

    async def _send_gap(
        self,
        uow: SqlAlchemyUnitOfWork,
        sub: ChannelSub,
        channel: Channel,
        head: Head,
        since: str | None,
    ) -> None:
        if since is None:
            return
        position = parse_cursor(since)
        if position is None:
            self._resync(sub, ResyncReason.CURSOR_EXPIRED)
            return
        since_rev, since_seq = position
        if since_rev >= head.rev:
            return
        limit = self._settings.live_max_changes_per_batch
        lines = await LineRepository(uow.session).changed_since(
            channel.id, after_rev=since_rev, limit=limit + 1
        )
        if len(lines) > limit:
            self._resync(sub, ResyncReason.TOO_MANY)
            return
        events: list[str] = []
        if head.changed_rev > since_rev:
            events.append(await channel_updated(uow, sub.caller, channel))
        view = await lines_view(uow, channel, lines, since_seq=since_seq)
        events.extend(view.events_for(sub.user_id))
        sub.connection.offer_events(sub.id, head.cursor, events, previous_cursor=since)

    async def join_inbox(
        self, connection: LiveConnection, subscription_id: str, *, caller: Caller
    ) -> InboxSub:
        self._joining += 1
        self.idle_since = None
        try:
            await self._ensure_started()
            async with self._lock, self._uow_factory() as uow:
                sub = InboxSub(
                    id=subscription_id,
                    connection=connection,
                    caller=caller,
                    on_release=self._release_inbox,
                )
                connection.subscriptions[subscription_id] = sub
                connection.offer_subscribed(subscription_id, None)
                if not connection.closed:
                    await self.inbox.join(uow, sub)
                    # Their answers are followed from now, so the channel
                    # they open next is sent each whole.
                    self._answers.follow_for(sub.user_id)
                return sub
        finally:
            self._joining -= 1
            self._maybe_idle()

    def _release_channel(self, sub: ChannelSub) -> None:
        subs = self._channels.get(sub.channel_id)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                self._channels.pop(sub.channel_id, None)
                self._heads.pop(sub.channel_id, None)
        self._maybe_idle()

    def _release_inbox(self, sub: InboxSub) -> None:
        self.inbox.release(sub)
        self._maybe_idle()

    # -- signals ----------------------------------------------------------------

    def on_signal(self, signal: Signal) -> None:
        kind = signal.get("k")
        channel_id = as_uuid(signal.get("c"))
        if channel_id is None:
            return
        if kind == "channel":
            self._dirty.add(channel_id)
            self._wake.set()
        elif kind == "read":
            user_id = as_uuid(signal.get("u"))
            if user_id is not None:
                self._reads.add((channel_id, user_id))
                self._wake.set()
        elif kind == "typing":
            self._on_typing(channel_id, signal)
        elif kind == "answering":
            self._on_answering(channel_id, signal)

    def catch_up(self) -> None:
        """Signals may have been missed: compare everything watched."""
        self._everything = True
        self._wake.set()

    def _on_typing(self, channel_id: UUID, signal: Signal) -> None:
        typist = as_uuid(signal.get("u"))
        event = typing_event(channel_id, signal.get("who"))
        for sub in list(self._channels.get(channel_id, ())):
            if sub.user_id != typist:
                sub.connection.offer_events(sub.id, sub.cursor, [event])

    def _on_answering(self, channel_id: UUID, signal: Signal) -> None:
        answering = answering_of(channel_id, signal)
        if answering is None:
            return
        state = "started" if signal.get("state") == "started" else "finished"
        for sub in list(self._channels.get(channel_id, ())):
            sub.connection.offer_events(
                sub.id, sub.cursor, [answering_for(state, answering, sub.user_id)]
            )
        if state == "finished":
            self._answers.finished(answering)
            return
        self._answers.started(answering)
        if self._has_reader(answering.for_user_id):
            self._answers.follow(answering)

    def _has_reader(self, user_id: UUID) -> bool:
        return user_id in self.inbox.subs or any(
            sub.user_id == user_id for subs in self._channels.values() for sub in subs
        )

    def _deliver_to(self, channel_id: UUID, user_id: UUID, event: str) -> None:
        for sub in list(self._channels.get(channel_id, ())):
            if sub.user_id == user_id:
                sub.connection.offer_events(sub.id, sub.cursor, [event])

    # -- the loop ---------------------------------------------------------------

    async def _run(self, *, after: float = 0.0) -> None:
        if after:
            await asyncio.sleep(after)
            # What was missed while it was down is found by comparing all.
            self._everything = True
        failures = 0
        while True:
            polled = await self._wait()
            started = time.monotonic()
            try:
                if time.monotonic() >= self._next_epoch_check:
                    await self._check_epoch()
                async with self._lock, self._uow_factory() as uow:
                    await self._drain(uow, polled=polled)
            except (SQLAlchemyError, RedisError, OSError, TimeoutError) as exc:
                # What was not read is read on the next wake or poll: the
                # hub's cursors only move past what it sent.
                failures += 1
                logger.error(
                    "channels.live.hub_drain_failed",
                    pod_id=str(self.pod_id),
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
            else:
                failures = self._loop_failures = 0
                hub_drain_seconds.record(time.monotonic() - started)
                continue
            self._everything = True
            await asyncio.sleep(self._backoff(failures))

    async def _wait(self) -> bool:
        """Sleep until there is something to read; whether a poll is due."""
        timeout = max(
            0.0, min(self._next_poll, self._next_epoch_check) - time.monotonic()
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=timeout)
        # Reads are a coalescing window apart: signals landing in the window
        # ride one read.
        gap = self._last_read + self._settings.live_wake_coalesce_ms / 1000
        if gap > time.monotonic():
            await asyncio.sleep(gap - time.monotonic())
        self._wake.clear()
        self._last_read = time.monotonic()
        if self._last_read >= self._next_poll:
            self._next_poll = self._last_read + self._settings.live_idle_poll_seconds
            return True
        return False

    async def _drain(self, uow: SqlAlchemyUnitOfWork, *, polled: bool) -> None:
        everything = polled or self._everything
        self._everything = False
        dirty, self._dirty = self._dirty, set()
        reads, self._reads = self._reads, set()
        hub_reads.add(1)
        if everything:
            dirty |= set(self._channels)
            reads |= await self._reads_moved(uow)
        watched = dirty & set(self._channels)
        heads = await heads_of(uow, watched)
        for channel_id in sorted(watched):
            await self._advance(uow, channel_id, heads.get(channel_id))
        await self.inbox.drain(uow, dirty=dirty, reads=reads, everything=everything)
        await self._send_reads(uow, reads)

    async def _advance(
        self, uow: SqlAlchemyUnitOfWork, channel_id: UUID, head: Head | None
    ) -> None:
        """Send one channel's changes since the hub's cursor for it, up to
        ``head`` (``None``: the channel is gone)."""
        subs = [sub for sub in self._channels.get(channel_id, ()) if sub.active]
        known = self._heads.get(channel_id)
        if known is not None and head is not None and head.rev <= known.rev:
            return
        channel = await ChannelRepository(uow.session).get(channel_id)
        if head is None or channel is None:
            for sub in subs:
                self._end(sub, UnsubscribedReason.GONE.value)
            return
        since = known or Head(0, 0)
        self._heads[channel_id] = head
        limit = self._settings.live_max_changes_per_batch
        lines = await LineRepository(uow.session).changed_since(
            channel_id, after_rev=since.rev, limit=limit + 1
        )
        view = (
            await lines_view(uow, channel, lines, since_seq=since.seq)
            if len(lines) <= limit
            else None
        )
        members = await MemberRepository(uow.session).users_in(
            channel_id, {sub.user_id for sub in subs}
        )
        for sub in subs:
            if not may_see(
                channel, sub.caller.viewer, is_member=sub.user_id in members
            ):
                self._end(sub, UnsubscribedReason.ACCESS_REVOKED.value)
                continue
            previous, sub.cursor = sub.cursor, head.cursor
            if view is None:
                self._resync(sub, ResyncReason.TOO_MANY)
                continue
            events: list[str] = []
            if head.changed_rev > since.rev:
                events.append(await channel_updated(uow, sub.caller, channel))
            events.extend(view.events_for(sub.user_id))
            sub.connection.offer_events(
                sub.id, head.cursor, events, previous_cursor=previous
            )

    async def _send_reads(
        self, uow: SqlAlchemyUnitOfWork, reads: set[tuple[UUID, UUID]]
    ) -> None:
        members = MemberRepository(uow.session)
        for channel_id, user_id in sorted(reads):
            subs = [
                sub
                for sub in self._channels.get(channel_id, ())
                if sub.active and sub.user_id == user_id
            ]
            if not subs:
                continue
            event = read_updated(
                channel_id,
                await members.read_seq(channel_id=channel_id, user_id=user_id),
            )
            for sub in subs:
                sub.connection.offer_events(sub.id, sub.cursor, [event])

    async def _reads_moved(self, uow: SqlAlchemyUnitOfWork) -> set[tuple[UUID, UUID]]:
        """Read positions of the hub's readers moved since its last poll."""
        users = {sub.user_id for subs in self._channels.values() for sub in subs}
        users |= self.inbox.users
        # The database's clock, which stamped the rows, not this process's.
        now = await uow.session.scalar(select(func.now()))
        since, self._reads_since = self._reads_since, now
        if not users or since is None:
            return set()
        state = ChannelReadStateModel
        rows = await uow.session.execute(
            select(state.channel_id, state.user_id).where(
                state.user_id.in_(sorted(users)),
                state.updated_at > since - _READ_OVERLAP,
            )
        )
        return {(row[0], row[1]) for row in rows}

    # -- authorization ------------------------------------------------------------

    async def _check_epoch(self) -> None:
        self._next_epoch_check = (
            time.monotonic() + self._settings.live_authz_check_seconds
        )
        epoch = await self._epoch_of(self.pod_id)
        if epoch is None or epoch == self._epoch:
            return
        self._epoch = epoch
        async with self._lock, self._uow_factory() as uow:
            await self._recheck(uow)
            await self.inbox.recheck(uow, self._end)

    async def _recheck(self, uow: SqlAlchemyUnitOfWork) -> None:
        """The pod's grants moved: rebuild every reader, and end what they may
        no longer see."""
        callers: dict[UUID, Caller | None] = {}
        channels = await ChannelRepository(uow.session).by_ids(set(self._channels))
        for channel_id, subs in list(self._channels.items()):
            channel = channels.get(channel_id)
            members = await MemberRepository(uow.session).users_in(
                channel_id, {sub.user_id for sub in subs}
            )
            for sub in list(subs):
                if sub.user_id not in callers:
                    callers[sub.user_id] = await caller_for(
                        uow, pod_id=self.pod_id, user_id=sub.user_id
                    )
                caller = callers[sub.user_id]
                if (
                    caller is None
                    or channel is None
                    or not may_see(
                        channel, caller.viewer, is_member=sub.user_id in members
                    )
                ):
                    self._end(sub, UnsubscribedReason.ACCESS_REVOKED.value)
                    continue
                sub.caller = caller

    def _end(self, sub: ChannelSub | InboxSub, reason: str) -> None:
        sub.connection.offer_unsubscribed(sub.id, reason)

    def _resync(self, sub: ChannelSub, reason: ResyncReason) -> None:
        hub_resyncs.add(1, {"reason": reason.value, "topic": "channel"})
        sub.connection.offer_resync(sub.id, sub.cursor, reason)


__all__ = ["ChannelsHub", "EpochReader", "SIGNAL_KINDS"]
