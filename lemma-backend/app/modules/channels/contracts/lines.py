"""Lines, for the modules that answer them: read one, find what a conversation
last heard, and link a line to the conversation answering it.

A submodule rather than ``contracts/__init__``: what answers lines in Lemma --
an agent named in a channel, the pod's agent in a member's own channel -- is
the only reader.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    SenderKind,
    Source,
)
from app.modules.channels.domain.projection import withheld_from
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.repository import ChannelRepository

#: The most channels a member's agent is shown at once.
MAX_CHANNELS = 100


async def line_by_id(
    uow: SqlAlchemyUnitOfWork, message_id: UUID
) -> ChannelMessage | None:
    return await LineRepository(uow.session).get(message_id)


async def last_heard_line(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> ChannelMessage | None:
    """The latest line a person said in this conversation -- in Lemma or on a
    platform -- which is where its answer belongs."""
    return await LineRepository(uow.session).last_heard(conversation_id)


async def line_went_out(
    uow: SqlAlchemyUnitOfWork,
    *,
    message_id: UUID,
    source_id: UUID,
    conversation_id: UUID | None = None,
    external_message_id: str | None = None,
    external_thread_id: str | None = None,
) -> None:
    """A line written in Lemma went out at a place its delivery opened -- a
    first email -- and is that place's first line: a reply continues it."""
    await LineRepository(uow.session).went_out(
        message_id,
        source_id=source_id,
        conversation_id=conversation_id,
        external_message_id=external_message_id,
        external_thread_id=external_thread_id,
    )


async def thread_root_at(
    uow: SqlAlchemyUnitOfWork,
    *,
    source_id: UUID,
    external_thread_id: str | None,
    external_message_id: str | None = None,
) -> UUID | None:
    """The line a platform thread hangs from, so its replies are a thread here.

    ``None`` for a message that starts its thread, or a thread whose first
    message the pod never heard.
    """
    if not external_thread_id or external_thread_id == external_message_id:
        return None
    return await LineRepository(uow.session).at(source_id, external_thread_id)


async def line_said_in_run(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, agent_run_id: UUID, text: str
) -> ChannelMessage | None:
    """What one agent run already wrote in this channel, word for word: a
    retried job finds its line instead of writing it twice."""
    return await LineRepository(uow.session).said_in_run(
        channel_id=channel_id, agent_run_id=agent_run_id, text=text
    )


async def claim_line(
    uow: SqlAlchemyUnitOfWork, *, message_id: UUID, conversation_id: UUID
) -> None:
    """Link a line to the conversation that answers it."""
    await LineRepository(uow.session).claim(message_id, conversation_id=conversation_id)


async def member_background(
    uow: SqlAlchemyUnitOfWork, *, line: ChannelMessage, for_user_id: UUID, limit: int
) -> list[ChannelMessage]:
    """What a member's run answering ``line`` is shown of the channel around it.

    The thread it is in, or the channel's recent lines -- but never what a
    contact wrote (a run with a member's access is not steered by somebody
    outside the pod), an answer made with another member's own access, or a
    deleted line. Oldest first, without ``line`` itself.
    """
    lines = LineRepository(uow.session)
    root_id = line.thread_root_id
    if root_id is not None:
        root = await lines.get(root_id)
        around = ([root] if root else []) + await lines.thread(root_id, limit=limit)
    else:
        around = await lines.recent(line.channel_id, limit=limit, top_level=True)
    return [
        each
        for each in around
        if each.id != line.id
        and not each.is_deleted
        and each.sender.kind is not SenderKind.CONTACT
        and not withheld_from(each, user_id=for_user_id)
    ][-limit:]


async def channels_of_member(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    user_id: UUID,
    sees_contacts: bool,
    kind: ChannelKind | None = None,
) -> list[tuple[Channel, list[Source]]]:
    """The channels a member sees, most recently active first, with the
    places feeding each."""
    kinds = (
        (kind,)
        if kind
        else (ChannelKind.DIRECT, ChannelKind.GROUP, ChannelKind.CONTACT)
    )
    if not sees_contacts:
        kinds = tuple(each for each in kinds if each is not ChannelKind.CONTACT)
    if not kinds:
        return []
    repository = ChannelRepository(uow.session)
    channels = await repository.list_for_member(
        pod_id=pod_id, user_id=user_id, kinds=kinds, before=None, limit=MAX_CHANNELS
    )
    sources = await repository.sources_of_many([channel.id for channel in channels])
    return [(channel, sources.get(channel.id, [])) for channel in channels]


async def member_may_read(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    channel_id: UUID,
    user_id: UUID,
    sees_contacts: bool,
) -> Channel | None:
    """The channel, when this member may read it: the pod's, not another
    member's own, and a contact's only for who may see contacts."""
    channel = await ChannelRepository(uow.session).get(channel_id)
    if channel is None or channel.pod_id != pod_id:
        return None
    if channel.kind is ChannelKind.DIRECT and channel.member_user_id != user_id:
        return None
    if channel.kind is ChannelKind.CONTACT and not sees_contacts:
        return None
    return channel


async def recent_lines(
    uow: SqlAlchemyUnitOfWork, *, channel_id: UUID, limit: int
) -> list[ChannelMessage]:
    """A channel's newest lines outside threads, oldest first."""
    return await LineRepository(uow.session).recent(
        channel_id, limit=limit, top_level=True
    )


async def search_lines(
    uow: SqlAlchemyUnitOfWork, *, channel_ids: list[UUID], words: str, limit: int
) -> list[ChannelMessage]:
    return await LineRepository(uow.session).search(
        channel_ids=channel_ids, words=words, limit=limit
    )


__all__ = [
    "channels_of_member",
    "claim_line",
    "last_heard_line",
    "line_by_id",
    "member_background",
    "member_may_read",
    "recent_lines",
    "search_lines",
]
