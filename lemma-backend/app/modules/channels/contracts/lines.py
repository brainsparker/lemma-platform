"""Lines, for the modules that answer them: read one, find what a conversation
last heard, and link a line to the conversation answering it.

A submodule rather than ``contracts/__init__``: what answers lines in Lemma --
an agent named in a channel, the pod's agent in a member's own channel -- is
the only reader.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import ChannelMessage, SenderKind
from app.modules.channels.domain.projection import withheld_from
from app.modules.channels.infrastructure.lines import LineRepository


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


__all__ = ["claim_line", "last_heard_line", "line_by_id", "member_background"]
