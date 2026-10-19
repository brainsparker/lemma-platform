"""What the live inbox costs a reader when many channels move at once.

The ``inbox`` topic works each reader's counts out again from the tables
whenever channels move (``services/live/inbox_feed``). A burst -- an agent
answering in twenty channels, a sweep closing questions across a pod -- moves
many channels in one drain, and a recompute that reads per channel per reader
costs channels x readers statements, which is the shape that holds a hub's
lock for the whole burst.

Statements rather than time, for the reason ``test_support.query_counting``
gives. What is
held is the cost one more reader adds: it may not grow with the number of
channels that moved. What every reader shares -- the channels' heads, the
moved lines named once -- is printed beside it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.live.connection import LiveConnection
from app.core.live.protocol import ResyncReason
from app.modules.channels.services.live.inbox_feed import InboxFeed
from app.modules.channels.services.live.readers import caller_for
from app.modules.channels.services.live.subscriptions import InboxSub
from app.modules.channels.tests.e2e.channel_pod import Pod

pytestmark = pytest.mark.e2e

#: The most channels moved in one drain, and the fewest.
MANY, ONE = 20, 1


@contextmanager
def _counted_on(connection: AsyncConnection) -> Iterator[list[str]]:
    """Statements issued on this one connection while the block runs.

    Narrower than ``query_counting.counted_queries``, which hears every engine
    in the process: anything else the app runs meanwhile -- a sweep, a cache
    fill -- lands in that count, and a drain's is exact or it is nothing.
    """
    statements: list[str] = []

    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    target = connection.sync_connection
    assert target is not None
    event.listen(target, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(target, "before_cursor_execute", before)


class _Socket:
    """Where a reader's events would be sent: kept, to check they were."""

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        self.events: list[str] = []

    def offer_events(
        self, subscription_id: str, cursor: str | None, events: list[str]
    ) -> None:
        self.events.extend(events)

    def offer_resync(
        self, subscription_id: str, cursor: str | None, reason: ResyncReason
    ) -> None:
        raise AssertionError(f"a drain asked for a resync: {reason}")


async def _groups(db_session, pod: Pod, readers: Sequence[UUID]) -> list[UUID]:
    """``MANY`` public groups, every reader in each, a line said in each."""
    pod_id = UUID(pod.id)
    ids = [uuid4() for _ in range(MANY)]
    await db_session.execute(
        text("""
        INSERT INTO channels
          (id, pod_id, kind, visibility, title, last_seq, last_rev, created_at,
           updated_at)
        SELECT id, :pod, 'GROUP', 'PUBLIC', 'burst', 0, 0, now(), now()
        FROM unnest(CAST(:ids AS uuid[])) id
        """),
        {"pod": pod_id, "ids": ids},
    )
    await db_session.execute(
        text("""
        INSERT INTO channel_members
          (id, channel_id, pod_id, user_id, role, notify, created_at, updated_at)
        SELECT gen_random_uuid(), c, :pod, u, 'member', 'ALL', now(), now()
        FROM unnest(CAST(:ids AS uuid[])) c, unnest(CAST(:readers AS uuid[])) u
        """),
        {"pod": pod_id, "ids": ids, "readers": list(readers)},
    )
    await db_session.commit()
    await _say_in(db_session, ids, writer=readers[0])
    return ids


async def _say_in(db_session, channel_ids: Sequence[UUID], *, writer: UUID) -> None:
    """A line in each channel, numbered and counted as a post numbers it."""
    await db_session.execute(
        text("""
        WITH moved AS (
          UPDATE channels
          SET last_seq = last_seq + 1, last_rev = last_rev + 1, last_line_at = now()
          WHERE id = ANY(CAST(:ids AS uuid[]))
          RETURNING id, pod_id, last_seq, last_rev
        )
        INSERT INTO channel_messages
          (id, channel_id, pod_id, seq, sender_kind, sender_user_id, body, rev,
           created_at, updated_at)
        SELECT gen_random_uuid(), id, pod_id, last_seq, 'MEMBER', :writer,
               'news ' || last_seq, last_rev, now(), now()
        FROM moved
        """),
        {"ids": list(channel_ids), "writer": writer},
    )
    await db_session.commit()


async def _drain_cost(
    db_manager, db_session, pod: Pod, readers: Sequence[UUID], moved: list[UUID]
) -> int:
    """Statements one drain issues for ``readers`` after ``moved`` changed --
    every reader told of every channel."""
    pod_id = UUID(pod.id)
    feed = InboxFeed(pod_id)
    sockets: list[_Socket] = []
    async with db_manager.session_factory() as session:
        uow = SqlAlchemyUnitOfWork(session)
        for user_id in readers:
            caller = await caller_for(uow, pod_id=pod_id, user_id=user_id)
            assert caller is not None
            socket = _Socket(user_id)
            sockets.append(socket)
            await feed.join(
                uow,
                InboxSub(
                    id=uuid4().hex,
                    connection=cast(LiveConnection, socket),
                    caller=caller,
                ),
            )
    await _say_in(db_session, moved, writer=readers[0])
    async with db_manager.session_factory() as session:
        with _counted_on(await session.connection()) as statements:
            await feed.drain(
                SqlAlchemyUnitOfWork(session),
                dirty=moved,
                reads=(),
                everything=False,
            )
    for socket in sockets:
        told = "".join(socket.events)
        missed = [channel for channel in moved if str(channel) not in told]
        assert not missed, f"reader {socket.user_id} was not told of {missed}"
    return len(statements)


async def test_one_more_reader_costs_the_same_however_many_channels_moved(
    pod: Pod, db_session, db_manager
):
    everyone = [
        UUID(person["id"]) for person in (pod.owner, pod.editor, pod.user, pod.viewer)
    ]
    channels = await _groups(db_session, pod, everyone)

    cost = {
        (len(readers), len(moved)): await _drain_cost(
            db_manager, db_session, pod, readers, moved
        )
        for readers in (everyone[:1], everyone)
        for moved in (channels[:ONE], channels[:MANY])
    }
    many_readers = len(everyone)
    per_reader = {
        moved: (cost[many_readers, moved] - cost[1, moved]) / (many_readers - 1)
        for moved in (ONE, MANY)
    }
    shared = {moved: cost[1, moved] - per_reader[moved] for moved in (ONE, MANY)}
    print(
        f"\n  statements per drain: {cost}"
        f"\n  per reader, by channels moved: {per_reader}"
        f"\n  shared by every reader, by channels moved: {shared}"
    )
    assert per_reader[MANY] == per_reader[ONE], (
        f"a reader costs {per_reader[ONE]:g} statements when {ONE} channel "
        f"moves and {per_reader[MANY]:g} when {MANY} do: the recompute reads "
        "per channel for every reader, where it should read every moved "
        "channel's counts for a reader at once"
    )
