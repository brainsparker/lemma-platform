"""A person's conversation with an agent, inside a channel.

A conversation is one agent working for one person -- a member, or a contact
-- and in a channel there is one per agent and person: however many threads,
numbers or days the person writes across, their messages there continue it.
The surface that hears a message asks for that conversation here, and places a
conversation it opened in its channel.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import ConversationModel


async def conversation_in_channel(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    user_id: UUID,
    contact_id: UUID | None,
) -> UUID | None:
    """The person's live conversation with this agent in the channel, if any.

    The person is the contact when ``contact_id`` is set -- whoever looks after
    them -- and otherwise the member ``user_id`` names. An archived
    conversation, or a sub-agent's, is never continued.
    """
    conversation = ConversationModel
    agent = effective_agent_id(agent_id, pod_id=pod_id)
    person = (
        conversation.contact_id == contact_id
        if contact_id is not None
        else (conversation.user_id == user_id) & conversation.contact_id.is_(None)
    )
    return await uow.session.scalar(
        select(conversation.id)
        .where(
            conversation.channel_id == channel_id,
            conversation.pod_id == pod_id,
            func.coalesce(conversation.agent_id, conversation.pod_id) == agent,
            person,
            conversation.parent_id.is_(None),
            conversation.is_archived.is_(False),
        )
        .order_by(conversation.last_activity_at.desc())
        .limit(1)
    )


async def place_in_channel(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, channel_id: UUID
) -> None:
    """Put a conversation that has no channel yet in this one."""
    await uow.session.execute(
        update(ConversationModel)
        .where(
            ConversationModel.id == conversation_id,
            ConversationModel.channel_id.is_(None),
        )
        .values(channel_id=channel_id)
    )


async def channel_of_conversation(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> UUID | None:
    """The channel a conversation works in, or ``None``."""
    return await uow.session.scalar(
        select(ConversationModel.channel_id).where(
            ConversationModel.id == conversation_id
        )
    )
