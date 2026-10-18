"""A member writing in Lemma's own channels, answered by the pod's agents.

Lemma is one more place to talk to the pod. In a member's own channel the
pod's agent answers whatever they write, as it would their DM; anywhere else an
agent answers when the line names it. Each answers in the member's own
conversation with it in that channel -- with the member's access, like any
turn they start -- and its answer comes back where they wrote: a line in the
channel, in the same thread (``events.reply_for_run_work``).

The run is shown the channel around the line, as a member's run in a group is
(``channels.contracts.lines.member_background``).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import effective_agent_id
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts import AgentNotFoundError, channel_conversations
from app.modules.agent.contracts import conversations_for_surfaces as conversations
from app.modules.agent.contracts.agents import agent_id_for_name, agent_name_for_id
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    ChannelMessage,
    SenderKind,
    channel_by_id,
)
from app.modules.channels.contracts.lines import (
    claim_line,
    line_by_id,
    member_background,
)

logger = get_logger(__name__)

#: The most lines of the channel a run answering a line is shown.
IN_APP_CONTEXT_LINES = 30
#: The most agents one line may set answering.
MAX_ANSWERING_AGENTS = 3


def answering_agents(
    channel: Channel, line: ChannelMessage, mentioned: Sequence[UUID]
) -> list[UUID]:
    """Which agents answer this line: those it names, else -- in the member's
    own channel -- the pod's agent."""
    named = list(dict.fromkeys(mentioned))[:MAX_ANSWERING_AGENTS]
    if named:
        return named
    if channel.kind is ChannelKind.DIRECT and line.source_id is None:
        return [effective_agent_id(None, pod_id=channel.pod_id)]
    return []


async def answer_in_app(
    uow_factory: UnitOfWorkFactory, *, message_id: UUID, mentioned: Sequence[UUID]
) -> None:
    async with uow_factory() as uow:
        line = await line_by_id(uow, message_id)
        channel = await channel_by_id(uow, line.channel_id) if line else None
    if line is None or channel is None or line.members_only or line.is_deleted:
        return
    user_id = line.sender.user_id
    if line.sender.kind is not SenderKind.MEMBER or user_id is None:
        return
    for agent_id in answering_agents(channel, line, mentioned):
        async with uow_factory() as uow:
            await _answer_as(
                uow, channel=channel, line=line, user_id=user_id, agent_id=agent_id
            )


async def _agent_named(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, agent_id: UUID
) -> str | None:
    """The agent's name, if it is one of this pod's."""
    name = await agent_name_for_id(uow.session, agent_id)
    if name is None:
        return None
    try:
        found = await agent_id_for_name(uow.session, pod_id=pod_id, name=name)
    except AgentNotFoundError:
        return None
    return name if found == agent_id else None


async def _answer_as(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel: Channel,
    line: ChannelMessage,
    user_id: UUID,
    agent_id: UUID,
) -> None:
    pod_id = channel.pod_id
    agent_name = await _agent_named(uow, pod_id=pod_id, agent_id=agent_id)
    if agent_name is None:
        return
    context = await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=pod_id
    )
    token = set_current_context(context)
    try:
        # Authorized first, since that commits; then locked, looked for again,
        # and opened -- as a surface opens one (``ConversationBinder``).
        await conversations.authorize_surface_conversation(
            uow, pod_id=pod_id, agent_name=agent_name, user_id=user_id
        )
        conversation_id = await _members_conversation(
            uow,
            channel=channel,
            user_id=user_id,
            agent_id=agent_id,
            agent_name=agent_name,
        )
        await claim_line(uow, message_id=line.id, conversation_id=conversation_id)
        background = await member_background(
            uow, line=line, for_user_id=user_id, limit=IN_APP_CONTEXT_LINES
        )
        await conversations.start_surface_turn(
            uow,
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            content=line.text,
            agent_name=agent_name,
            message_metadata=_turn_metadata(channel, line, background),
        )
        await uow.commit()
    except DomainError as refused:
        logger.warning(
            "agent_surfaces.in_app_turn.refused.observed",
            pod_id=str(pod_id),
            channel_id=str(channel.id),
            agent_id=str(agent_id),
            reason=refused.code,
        )
    finally:
        reset_current_context(token)


async def _members_conversation(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel: Channel,
    user_id: UUID,
    agent_id: UUID,
    agent_name: str,
) -> UUID:
    await channel_conversations.lock_person_in_channel(
        uow,
        channel_id=channel.id,
        pod_id=channel.pod_id,
        agent_id=agent_id,
        person_id=user_id,
    )
    found = await channel_conversations.conversation_in_channel(
        uow,
        channel_id=channel.id,
        pod_id=channel.pod_id,
        agent_id=agent_id,
        user_id=user_id,
        contact_id=None,
    )
    if found is not None:
        return found
    opened = await conversations.open_surface_conversation(
        uow,
        pod_id=channel.pod_id,
        agent_name=agent_name,
        user_id=user_id,
        title=(channel.title or "Lemma")[:255],
        metadata={"source": "lemma_channel", "channel_kind": channel.kind.value},
        require_execute_grant=False,
        channel_id=channel.id,
    )
    return opened.id


def _turn_metadata(
    channel: Channel, line: ChannelMessage, background: Sequence[ChannelMessage]
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "source": "lemma_channel",
        "channel_id": str(channel.id),
        "channel_message_id": str(line.id),
    }
    if channel.title:
        metadata["channel_name"] = channel.title
    if line.sender.display_name:
        metadata["sender_display_name"] = line.sender.display_name
    if background:
        metadata["channel_context"] = [
            {
                "author": said.sender.display_name,
                "text": said.text,
                "ts": said.created_at.isoformat(),
                "outside_pod": False,
            }
            for said in background
        ]
    return metadata
