"""A channel's change counter, and the signal that tells its live hubs.

Every change a channel's live subscribers are shown takes the next number of
the channel (``channels.last_rev``) and stamps it on what changed -- the line
(``channel_messages.rev``), or the channel itself (``changed_rev``). The
number is taken by updating the channel row, which holds its lock until the
transaction ends, so numbers commit in order: a hub that has read up to ``n``
and finds ``n + 2`` committed will never later find ``n + 1``. A hub reads
``rev > n`` and is exact, however many signals it missed.

The counter is taken *before* the line or question it covers is touched, the
same order a new line takes the channel lock in (``LineRepository.append``),
so two writers never wait on each other's rows the other way round.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import Select, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live.signals import signal_after_commit
from app.modules.channels.infrastructure.message_models import ChannelMessageModel
from app.modules.channels.infrastructure.models import ChannelModel


def channel_signal(channel_id: UUID) -> dict[str, str]:
    """A channel's change counter moved: read it."""
    return {"k": "channel", "c": str(channel_id)}


def read_signal(channel_id: UUID, user_id: UUID) -> dict[str, str]:
    """A member's read position in a channel moved: read it."""
    return {"k": "read", "c": str(channel_id), "u": str(user_id)}


async def next_rev(
    session: AsyncSession, channel_id: UUID, *, channel_changed: bool = False
) -> int | None:
    """Take the channel's next change number; ``None`` for no such channel.

    ``channel_changed``: the change is to the channel's own fields or its
    members, which subscribers are sent as ``channel.updated``.
    """
    # ``updated_at`` by the database's clock, with every change number: a hub's
    # idle poll finds the channels that moved by it (``live.events.changed_heads``).
    values: dict[str, object] = {
        "last_rev": ChannelModel.last_rev + 1,
        "updated_at": func.now(),
    }
    if channel_changed:
        values["changed_rev"] = ChannelModel.last_rev + 1
    found = (
        await session.execute(
            update(ChannelModel)
            .where(ChannelModel.id == channel_id)
            .values(**values)
            .returning(ChannelModel.last_rev, ChannelModel.pod_id)
            .execution_options(synchronize_session=False)
        )
    ).first()
    if found is None:
        return None
    rev, pod_id = int(found[0]), found[1]
    signal_after_commit(session, pod_id, channel_signal(channel_id))
    return rev


async def touch_channel(session: AsyncSession, channel_id: UUID) -> None:
    """The channel's own fields or members changed."""
    await next_rev(session, channel_id, channel_changed=True)


async def touch_channels(
    session: AsyncSession, channel_ids: Select[tuple[UUID]]
) -> None:
    """Every channel ``channel_ids`` selects changed its members: one
    statement, its signals once it commits."""
    rows = await session.execute(
        update(ChannelModel)
        .where(ChannelModel.id.in_(channel_ids.scalar_subquery()))
        .values(
            last_rev=ChannelModel.last_rev + 1,
            changed_rev=ChannelModel.last_rev + 1,
            updated_at=func.now(),
        )
        .returning(ChannelModel.id, ChannelModel.pod_id)
        .execution_options(synchronize_session=False)
    )
    for channel_id, pod_id in rows:
        signal_after_commit(session, pod_id, channel_signal(channel_id))


async def touch_lines(session: AsyncSession, message_ids: Collection[UUID]) -> None:
    """These lines changed in a way their readers see (their question moved,
    say): stamp each with a new change of its channel."""
    if not message_ids:
        return
    rows = await session.execute(
        select(ChannelMessageModel.id, ChannelMessageModel.channel_id).where(
            ChannelMessageModel.id.in_(sorted(message_ids))
        )
    )
    by_channel: dict[UUID, list[UUID]] = {}
    for message_id, channel_id in rows:
        by_channel.setdefault(channel_id, []).append(message_id)
    # Channels in one order, so two batches touching the same two channels
    # take their locks the same way round.
    for channel_id in sorted(by_channel):
        rev = await next_rev(session, channel_id)
        if rev is None:
            continue
        await session.execute(
            update(ChannelMessageModel)
            .where(ChannelMessageModel.id.in_(by_channel[channel_id]))
            .values(rev=rev)
            .execution_options(synchronize_session=False)
        )


#: ``touch_lines`` for one line, in one statement: the channel's number is
#: taken (its row locked) in the first CTE, before the line is stamped.
_TOUCH_LINE = text(
    """
    WITH bumped AS (
        UPDATE channels SET last_rev = channels.last_rev + 1, updated_at = now()
        FROM channel_messages AS line
        WHERE line.id = :message_id AND channels.id = line.channel_id
        RETURNING channels.id, channels.pod_id, channels.last_rev
    )
    UPDATE channel_messages SET rev = bumped.last_rev
    FROM bumped
    WHERE channel_messages.id = :message_id
    RETURNING bumped.id, bumped.pod_id
    """
)


async def touch_line(session: AsyncSession, message_id: UUID) -> None:
    """One line changed in a way its readers see."""
    found = (await session.execute(_TOUCH_LINE, {"message_id": message_id})).first()
    if found is not None:
        signal_after_commit(session, found[1], channel_signal(found[0]))


__all__ = [
    "channel_signal",
    "next_rev",
    "read_signal",
    "touch_channel",
    "touch_channels",
    "touch_line",
    "touch_lines",
]
