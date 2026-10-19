"""A member writing in Lemma's own channels, answered by the pod's agents.

Lemma is one more place to talk to the pod. Channels decides which agents
answer a member's line (``ChannelMessagePosted.answering_agent_ids``: those it
names, those in the channel that answer every line, and in the member's own
channel the pod's agent); this module does the answering. Each agent answers in
the member's own conversation with it in that channel -- with the member's
access, like any turn they start -- and its answer comes back as a line in the
thread of the line it answers, said for them.

Both halves are safe to run twice. The turn is started with the line as its
client id, so a retried job finds the turn it already started; the answer is
posted with the run as its client id, so a retried post is the same line.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import effective_agent_id
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts import channel_conversations
from app.modules.agent.contracts.run_replies import (
    answer_line_client_id,
    run_reply_material,
)
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent.infrastructure.models.conversation import (
    ConversationModel,
    MessageModel,
)
from app.modules.agent.services.service_factories import build_conversation_service
from app.modules.channels.contracts import (
    Channel,
    ChannelMessage,
    NewLine,
    Principal,
    Sender,
    SenderKind,
    channel_by_id,
    post,
)
from app.modules.channels.contracts.lines import (
    claim_line,
    line_by_id,
    member_background,
)

logger = get_logger(__name__)

#: The most lines of the channel a run answering a line is shown.
IN_APP_CONTEXT_LINES = 30
#: What a turn started by a line in Lemma says it came from.
LEMMA_CHANNEL = "lemma_channel"
#: Where a person's own words came from, as their messages say: in Lemma, on
#: a platform, in a web chat. Anything else -- the news that answers arrived,
#: a notification -- is not somebody saying something to answer.
_SAID_BY_A_PERSON = (LEMMA_CHANNEL, "agent_surfaces", "web_widget")


def turn_client_id(message_id: UUID) -> str:
    """The turn a line starts, named by the line: a retry is the same turn."""
    return f"channel-line:{message_id}"


def answer_client_id(run_id: UUID) -> str:
    """The line a run's answer is, named by the run: a retry is the same line."""
    return answer_line_client_id(run_id)


async def answer_line(
    uow_factory: UnitOfWorkFactory, *, message_id: UUID, agent_id: UUID
) -> UUID | None:
    """Start ``agent_id``'s turn answering a member's line; the conversation it
    answers in, or ``None`` when it does not answer it."""
    async with uow_factory() as uow:
        line = await line_by_id(uow, message_id)
        channel = await channel_by_id(uow, line.channel_id) if line else None
    if line is None or channel is None or not _answerable(line):
        return None
    async with uow_factory() as uow:
        agent_name = await _pod_agent_name(
            uow, pod_id=channel.pod_id, agent_id=agent_id
        )
        if agent_name is None:
            logger.warning(
                "agent.channel_answer.not_a_pod_agent.observed",
                pod_id=str(channel.pod_id),
                channel_id=str(channel.id),
                agent_id=str(agent_id),
            )
            return None
        return await _answer_as(
            uow, channel=channel, line=line, agent_id=agent_id, agent_name=agent_name
        )


def _answerable(line: ChannelMessage) -> bool:
    """A member's own line written in Lemma, still there."""
    return (
        line.sender.kind is SenderKind.MEMBER
        and line.sender.user_id is not None
        and line.source_id is None
        and not line.members_only
        and not line.is_deleted
    )


async def _pod_agent_name(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, agent_id: UUID
) -> str | None:
    """The agent's stored name, if it is one of this pod's."""
    return await uow.session.scalar(
        select(AgentModel.name).where(
            AgentModel.id == agent_id, AgentModel.pod_id == pod_id
        )
    )


async def _answer_as(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel: Channel,
    line: ChannelMessage,
    agent_id: UUID,
    agent_name: str,
) -> UUID | None:
    pod_id, user_id = channel.pod_id, line.sender.user_id
    assert user_id is not None
    context = await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=pod_id
    )
    token = set_current_context(context)
    service = build_conversation_service(uow)
    try:
        # Authorized first, since that commits; then locked, looked for again,
        # and opened -- as a surface opens one.
        await service.authorize_opening(
            pod_id=pod_id, agent_name=agent_name, user_id=user_id
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
        await service.add_user_message_and_start_run(
            conversation_id=conversation_id,
            user_id=user_id,
            content=line.text,
            pod_id=pod_id,
            agent_name=agent_name,
            message_metadata=turn_metadata(channel, line, background),
            require_execute_grant=False,
            client_message_id=turn_client_id(line.id),
        )
        await uow.commit()
        return conversation_id
    except DomainError as refused:
        logger.warning(
            "agent.channel_answer.refused.observed",
            pod_id=str(pod_id),
            channel_id=str(channel.id),
            agent_id=str(agent_id),
            reason=refused.code,
        )
        return None
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
    opened = await build_conversation_service(uow).create_conversation(
        pod_id=channel.pod_id,
        agent_name=agent_name,
        user_id=user_id,
        title=(channel.title or "Lemma")[:255],
        metadata={"source": LEMMA_CHANNEL, "channel_kind": channel.kind.value},
        require_execute_grant=False,
        channel_id=channel.id,
    )
    return opened.id


def turn_metadata(
    channel: Channel, line: ChannelMessage, background: Sequence[ChannelMessage]
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "source": LEMMA_CHANNEL,
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


# -- the answer ------------------------------------------------------------------


async def line_answered_by(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> UUID | None:
    """The line in Lemma a run of this conversation answers, if it answers one.

    The last thing a person said in the conversation: a line in Lemma, or a
    message on a platform -- whose answer goes back there, not here.
    """
    metadata = MessageModel.message_metadata
    said = await uow.session.scalar(
        select(metadata)
        .where(
            MessageModel.conversation_id == conversation_id,
            MessageModel.role == "user",
            metadata["source"].astext.in_(_SAID_BY_A_PERSON),
        )
        .order_by(MessageModel.sequence.desc())
        .limit(1)
    )
    if not isinstance(said, dict) or said.get("source") != LEMMA_CHANNEL:
        return None
    try:
        return UUID(str(said.get("channel_message_id")))
    except ValueError:
        return None


async def post_run_answer(
    uow: SqlAlchemyUnitOfWork, *, run_id: UUID, conversation_id: UUID
) -> ChannelMessage | None:
    """A finished run's answer, as the agent's line in the thread of the line
    in Lemma it answers. Nothing for a run answering anything else."""
    line_id = await line_answered_by(uow, conversation_id)
    if line_id is None:
        return None
    material = await run_reply_material(uow, run_id)
    if material is None or material.private or not material.answer:
        return None
    line = await line_by_id(uow, line_id)
    conversation = await uow.session.get(ConversationModel, conversation_id)
    if line is None or conversation is None or line.is_deleted:
        return None
    agent_id = effective_agent_id(conversation.agent_id, pod_id=conversation.pod_id)
    agent_name = await uow.session.scalar(
        select(AgentModel.name).where(AgentModel.id == agent_id)
    )
    return await post(
        uow,
        channel_id=line.channel_id,
        line=NewLine(
            sender=Sender.agent(
                agent_id,
                on_behalf_of=Principal.member(conversation.user_id),
                display_name=agent_name,
            ),
            text=material.answer,
            client_message_id=answer_client_id(run_id),
            conversation_id=conversation_id,
            agent_run_id=run_id,
            thread_root_id=line.thread_root_id or line.id,
            reply_to_id=line.id,
        ),
    )


__all__ = [
    "IN_APP_CONTEXT_LINES",
    "LEMMA_CHANNEL",
    "answer_client_id",
    "answer_line",
    "line_answered_by",
    "turn_client_id",
    "post_run_answer",
    "turn_metadata",
]
