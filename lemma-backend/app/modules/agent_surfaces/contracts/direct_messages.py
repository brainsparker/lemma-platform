"""Reaching a member of the pod, for the modules that do: agents, the API.

A message is a line in the member's own channel, carried to them on the chat
app they last used or by email (``services.direct_messages``). One that wants
an answer waits on them; when every question a conversation asked has been
answered, that conversation gets its turn back (``events.asked_questions_work``).
Each function opens its own unit of work: a message is not part of the asking
run's transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory


def _messages(uow: SqlAlchemyUnitOfWork):
    from app.modules.agent_surfaces.composition import build_direct_messages

    return build_direct_messages(uow)


@dataclass(frozen=True, slots=True)
class Sent:
    """What became of a message, in the terms an agent is told."""

    message_id: UUID
    channel_id: UUID
    delivered_via: str | None
    reason: str | None
    repeated: bool


async def resolve_recipient(*, pod_id: UUID, reference: str) -> UUID | None:
    """A pod member id, user id, or email address → a user id in this pod."""
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        return await _messages(uow).membership.resolve_pod_recipient(
            pod_id=pod_id, reference=reference
        )


async def send_direct_message(
    *,
    pod_id: UUID,
    recipient_user_id: UUID,
    text: str,
    actor_user_id: UUID | None,
    actor_agent_id: UUID | None,
    agent_name: str | None,
    origin_conversation_id: UUID | None,
    origin_run_id: UUID | None,
    origin_surface_id: UUID | None,
    wants_answer: bool,
    key: str | None,
    platform: str | None = None,
    title: str | None = None,
) -> Sent:
    from app.modules.agent_surfaces.services.direct_messages import DirectMessage

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        result = await _messages(uow).send(
            DirectMessage(
                pod_id=pod_id,
                recipient_user_id=recipient_user_id,
                text=text,
                actor_user_id=actor_user_id,
                actor_agent_id=actor_agent_id,
                agent_name=agent_name,
                origin_conversation_id=origin_conversation_id,
                origin_run_id=origin_run_id,
                origin_surface_id=origin_surface_id,
                platform=platform,
                wants_answer=wants_answer,
                key=key,
                title=title,
            )
        )
        return Sent(
            message_id=result.line.id,
            channel_id=result.line.channel_id,
            delivered_via=result.delivered.platform,
            reason=result.delivered.reason,
            repeated=result.repeated,
        )


async def reachable_channels(
    *,
    pod_id: UUID,
    recipients: dict[UUID, str | None],
    actor_agent_id: UUID | None,
) -> dict[UUID, list[str]]:
    """``{user_id: platforms}`` this agent can reach each person on right now.

    Degrades to "we do not know" on an infrastructure failure: an agent that
    cannot see the member list because reachability broke is worse off than
    one that sees the list without it.
    """
    try:
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            return await _messages(uow).channels.reachable_channels(
                pod_id=pod_id, recipients=recipients, actor_agent_id=actor_agent_id
            )
    except SQLAlchemyError, OSError:
        return {}


async def tell_member(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, user_id: UUID, text: str
) -> None:
    """A notice to one member, in their own channel: never pushed."""
    from app.modules.agent_surfaces.services.direct_messages import (
        tell_member as notice,
    )

    await notice(uow, pod_id=pod_id, user_id=user_id, text=text)


async def ask_a_person(
    *, pod_id: UUID, conversation_id: UUID, keeper_user_id: UUID, reason: str
) -> bool:
    """A contact's run calls for a person: their channel is flagged as waiting
    on one, a note there says why -- to the pod's members only -- and the
    member who looks after them is told where to look. Whether the
    conversation is in a channel to flag."""
    from app.core.authorization.delegation import effective_agent_id
    from app.modules.agent.contracts import channel_conversations
    from app.modules.agent_surfaces.services.direct_messages import (
        tell_member as notice,
    )
    from app.modules.channels.contracts import (
        Principal,
        Sender,
        call_for_person,
        post,
    )

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        channel_id = await channel_conversations.channel_of_conversation(
            uow, conversation_id
        )
        if channel_id is None:
            return False
        await call_for_person(uow, channel_id=channel_id, reason=reason[:255])
        await post(
            uow,
            channel_id=channel_id,
            sender=Sender.agent(
                effective_agent_id(None, pod_id=pod_id),
                on_behalf_of=Principal.member(keeper_user_id),
            ),
            text=f"This needs a person: {reason}",
            conversation_id=conversation_id,
            members_only=True,
        )
        await notice(
            uow,
            pod_id=pod_id,
            user_id=keeper_user_id,
            # Says where, not what: why is in their channel, which goes with
            # them when they are forgotten; a line here would outlive them.
            text=(
                "A contact you look after needs a person -- their channel is in "
                "Contacts, waiting on one, with what the bot said."
            ),
        )
        await uow.commit()
    return True


__all__ = [
    "Sent",
    "ask_a_person",
    "reachable_channels",
    "resolve_recipient",
    "send_direct_message",
    "tell_member",
]
