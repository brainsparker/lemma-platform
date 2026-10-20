"""A member writing in Lemma's own channels, answered by the pod's agents.

Lemma is one more place to talk to the pod. Channels decides which agents
answer a member's line (``ChannelMessagePosted.answering_agent_ids``: those it
names, those in the channel that answer every line, and in the member's own
channel the pod's agent); this module does the answering. Each agent answers in
the member's own conversation with it in that channel -- with the member's
access, like any turn they start -- and its answer comes back as a line in the
thread of the line it answers, said for them.

The turn records the line it answers (``channels.contracts.answers``), and
the run that ends answers exactly those lines: a member who writes L1 and then
L2 while the agent works on L1 has the answer under L1, where they asked. What
the run is shown of the channel around the line is kept as which lines, never
what they said (``channels.contracts.backgrounds``).

Both halves are safe to run twice. The turn is started with the line as its
client id, so a retried job finds the turn it already started; the answer is
posted with the run as its client id, so a retried post is the same line.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import effective_agent_id, is_pod_default_agent
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.text.thinking_tags import strip_context_echo, strip_thinking_tokens
from app.modules.agent.contracts import channel_conversations
from app.modules.agent.contracts.run_replies import (
    answer_line_client_id,
    run_reply_material,
)
from app.modules.agent.domain.queued_messages import (
    DURING_ACTIVE_RUN,
    STEERED_INTO_RUN,
)
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent.infrastructure.models.conversation import (
    ConversationModel,
    MessageModel,
)
from app.modules.agent.services.live_conversation import CONVERSATION_STREAM
from app.modules.agent.services.service_factories import build_conversation_service
from app.modules.channels.contracts import (
    ChannelKind,
    Channel,
    ChannelMessage,
    NewLine,
    Principal,
    Sender,
    SenderKind,
    channel_by_id,
    post,
)
from app.modules.channels.contracts.answers import (
    answers_of_run,
    settle_answers,
    start_answering,
)
from app.modules.channels.contracts.backgrounds import (
    BACKGROUND_KEY,
    BackgroundRefs,
    BackgroundView,
    refs_of,
)
from app.modules.channels.contracts.live import announce_answering
from app.modules.channels.contracts.lines import line_by_id, member_background

logger = get_logger(__name__)

#: The most lines of the channel a run answering a line is shown.
IN_APP_CONTEXT_LINES = 30
#: What a turn started by a line in Lemma says it came from.
LEMMA_CHANNEL = "lemma_channel"
#: The most messages one run is taken to have read: a burst steered into it.
MAX_LINES_A_RUN_READS = 50


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
    """A member's own line written in Lemma -- wherever it was carried -- still
    there. One heard on a platform is answered there."""
    return (
        line.sender.kind is SenderKind.MEMBER
        and line.sender.user_id is not None
        and not line.heard_outside
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
        await start_answering(
            uow,
            message_id=line.id,
            channel_id=line.channel_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )
        background = await member_background(uow, line=line, limit=IN_APP_CONTEXT_LINES)
        started = await service.add_user_message_and_start_run(
            conversation_id=conversation_id,
            user_id=user_id,
            content=line.text,
            pod_id=pod_id,
            agent_name=agent_name,
            message_metadata=turn_metadata(channel, line, background),
            require_execute_grant=False,
            client_message_id=turn_client_id(line.id),
        )
        # The run that answers it: the one this turn started, or the one
        # already going that it joined.
        await start_answering(
            uow,
            message_id=line.id,
            channel_id=line.channel_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            run_id=started.agent_run_id,
        )
        await uow.commit()
        announce_answering(
            pod_id=pod_id,
            channel_id=channel.id,
            message_id=line.id,
            agent_id=agent_id,
            for_user_id=user_id,
            state="started",
            conversation_id=conversation_id,
            agent_run_id=started.agent_run_id,
            stream=(CONVERSATION_STREAM, str(conversation_id)),
        )
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
    if background and line.sender.user_id is not None:
        # Which lines, not what they said: read from the timeline whenever the
        # prompt is built, as this member may read them then.
        metadata[BACKGROUND_KEY] = BackgroundRefs(
            pod_id=channel.pod_id,
            channel_id=channel.id,
            view=BackgroundView.MEMBER_IN_LEMMA,
            user_id=line.sender.user_id,
            items=refs_of(background),
        ).model_dump(mode="json")
    return metadata


# -- the answer ------------------------------------------------------------------


async def post_run_answer(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation_id: UUID,
    answered: bool = True,
    paused: bool = False,
) -> ChannelMessage | None:
    """A finished run's answer, as the agent's line in the thread of the
    oldest line in Lemma it answers. Nothing for a run answering none.

    ``answered``: the run ended with an answer, rather than failing, stopping
    or pausing; either way the lines' readers hear they are no longer being
    answered, once this commits. ``paused``: it is waiting on somebody, and
    the run that carries it on answers the same lines.
    """
    read, carries_on = await _lines_read_by(uow, run_id, conversation_id)
    answers = await answers_of_run(
        uow, conversation_id=conversation_id, line_ids=read, carries_on=carries_on
    )
    conversation = await uow.session.get(ConversationModel, conversation_id)
    if not answers or conversation is None:
        return None
    lines = [await line_by_id(uow, answer.message_id) for answer in answers]
    said = [line for line in lines if line is not None]
    if conversation.user_id is not None:
        for line in said:
            announce_answering(
                pod_id=conversation.pod_id,
                channel_id=line.channel_id,
                message_id=line.id,
                agent_id=answers[0].agent_id,
                for_user_id=conversation.user_id,
                state="finished",
                after_commit_of=uow.session,
            )
    if paused:
        return None
    posted = (
        await _post_answer(uow, run_id=run_id, conversation=conversation, line=said[0])
        if answered and said and not said[0].is_deleted
        else None
    )
    await settle_answers(
        uow, answers, run_id=run_id, answered_with=posted.id if posted else None
    )
    return posted


async def _lines_read_by(
    uow: SqlAlchemyUnitOfWork, run_id: UUID, conversation_id: UUID
) -> tuple[list[UUID], bool]:
    """The lines in Lemma whose words this run read -- the one that started
    it and any steered into it, oldest first -- and whether it carries on a
    paused run, which no message of its own started.

    Not a message that joined it and was never read: the run that delivers it
    answers it (``conversation_turns.start_queued_followup``).
    """
    metadata = MessageModel.message_metadata
    started_it = (MessageModel.agent_run_id == run_id) & (
        func.coalesce(metadata[DURING_ACTIVE_RUN].astext, "false") != "true"
    )
    rows = (
        await uow.session.execute(
            select(
                metadata["source"].astext,
                metadata["channel_message_id"].astext,
                started_it,
            )
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == "user",
                started_it | (metadata[STEERED_INTO_RUN].astext == str(run_id)),
            )
            .order_by(MessageModel.sequence)
            .limit(MAX_LINES_A_RUN_READS)
        )
    ).all()
    lines: list[UUID] = []
    for source, line_id, _ in rows:
        if source == LEMMA_CHANNEL and line_id:
            try:
                lines.append(UUID(line_id))
            except ValueError:
                continue
    return lines, not any(row[2] for row in rows)


async def _post_answer(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation: ConversationModel,
    line: ChannelMessage,
) -> ChannelMessage | None:
    agent_id = effective_agent_id(conversation.agent_id, pod_id=conversation.pod_id)
    material = await run_reply_material(uow, run_id)
    if material is None or material.private or not material.answer:
        return None
    # The same boundary as a platform's answer: no reasoning, and none of the
    # platform's own context blocks a model repeated back.
    text = strip_context_echo(strip_thinking_tokens(material.answer))
    if not text:
        return None
    agent_name = (
        None  # the pod's own assistant is named by the channel, as the pod
        if is_pod_default_agent(agent_id, pod_id=line.pod_id)
        else await uow.session.scalar(
            select(AgentModel.name).where(AgentModel.id == agent_id)
        )
    )
    channel = await channel_by_id(uow, line.channel_id)
    in_thread, place = _where_the_answer_goes(channel, line)
    return await post(
        uow,
        channel_id=line.channel_id,
        line=NewLine(
            sender=Sender.agent(
                agent_id,
                on_behalf_of=Principal.member(conversation.user_id),
                display_name=agent_name,
            ),
            text=text,
            target_source_id=place,
            client_message_id=answer_client_id(run_id),
            conversation_id=conversation.id,
            agent_run_id=run_id,
            thread_root_id=in_thread,
            reply_to_id=line.id,
        ),
        deliver_to=[place] if place else [],
    )


def _where_the_answer_goes(
    channel: Channel | None, line: ChannelMessage
) -> tuple[UUID | None, UUID | None]:
    """The thread an answer is a reply in, and the place it is carried to.

    A member's own channel reads as a chat: the answer follows the line it
    answers. Elsewhere it is a reply in the line's thread. A group's line
    carried to its platform was asked there too: the answer follows it, into
    its thread there. A contact's channel never: the answer is the member's,
    made with their access, not the contact's to read.
    """
    kind = channel.kind if channel is not None else None
    in_thread = line.thread_root_id or (None if kind is ChannelKind.DIRECT else line.id)
    return in_thread, line.place_id if kind is ChannelKind.GROUP else None


__all__ = [
    "IN_APP_CONTEXT_LINES",
    "LEMMA_CHANNEL",
    "answer_client_id",
    "answer_line",
    "turn_client_id",
    "post_run_answer",
    "turn_metadata",
]
