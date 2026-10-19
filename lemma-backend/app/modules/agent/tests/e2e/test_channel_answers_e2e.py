"""The pod's agents answering members in Lemma's channels, and asking them.

Against Postgres and the work runtime, with the model's part played by hand:
a run is created and finished with an answer, or with a paused call, exactly
as the runner would leave it. Everything the agent module does around it --
the line that sets an agent answering, the turn it starts, the answer posted
back, the questions a pause puts to the member and what answering them does --
runs for real, over the channels module's own contracts.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.core.infrastructure.work import WorkClass, WorkRegistry, facts
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    AgentRunStatus,
    ConversationStatus,
    MessageRole,
)
from app.modules.agent.events.channel_answer_work import (
    register_channel_answer_work,
)
from app.modules.agent.events.channel_question_work import (
    register_channel_question_work,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.channel_answers import answer_line, turn_client_id
from app.modules.agent.services.pause_questions import (
    sync_pause_questions,
    tool_ref,
)
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.services.service_factories import build_conversation_service
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    finish,
    messages,
    owner_uow,
    pausing_call,
    running_run,
    runs,
    seeded_conversation,
)
from app.modules.channels.contracts import (
    ChannelMessage,
    NewLine,
    Principal,
    QuestionKind,
    QuestionState,
    Sender,
    SenderKind,
    direct_channel,
    post,
)
from app.modules.channels.contracts import questions as channel_questions
from app.modules.channels.contracts.lines import line_by_id
from app.modules.channels.contracts.questions import AnswerRefused
from app.modules.channels.infrastructure.models import ChannelMessageModel

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


def _uows() -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


async def _member_says(pod_id: UUID, user_id: UUID, text: str) -> ChannelMessage:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        direct = await direct_channel(uow, pod_id=pod_id, user_id=user_id)
        line = await post(
            uow,
            channel_id=direct.id,
            line=NewLine(sender=Sender.member(user_id), text=text),
        )
        await uow.commit()
    assert line is not None
    return line


async def _drain(*classes: WorkClass) -> int:
    registry = WorkRegistry()
    register_channel_answer_work(registry)
    register_channel_question_work(registry)
    await make_due(async_session_maker)
    with suppress_agent_run_enqueue():
        return await run_until_idle(
            async_session_maker, registry=registry, classes=list(classes)
        )


async def _answering_conversation(line_id: UUID) -> UUID:
    async with async_session_maker() as session:
        conversation_id = await session.scalar(
            select(ChannelMessageModel.conversation_id).where(
                ChannelMessageModel.id == line_id
            )
        )
    assert conversation_id is not None, "no turn answers the line"
    return conversation_id


async def _finish_with(run_id: UUID, answer: str) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ConversationRepository(uow).finish_agent_run(
            agent_run_id=run_id,
            status=AgentRunStatus.COMPLETED,
            conversation_status=ConversationStatus.COMPLETED,
            output_data=answer,
        )
        await uow.commit()


def _finished(run: AgentRunModel, *, pod_id: UUID, user_id: UUID) -> AgentRunFinished:
    return AgentRunFinished(
        run_id=run.id,
        conversation_id=run.conversation_id,
        pod_id=pod_id,
        user_id=user_id,
        status="COMPLETED",
        conversation_status="COMPLETED",
        origin="chat",
    )


async def _lines_of_run(run_id: UUID) -> list[ChannelMessageModel]:
    async with async_session_maker() as session:
        return list(
            await session.scalars(
                select(ChannelMessageModel).where(
                    ChannelMessageModel.agent_run_id == run_id
                )
            )
        )


# -- answering a member's line -----------------------------------------------------


async def test_the_pods_agent_answers_a_line_once_however_often_it_is_retried(
    scenario,
):
    """The line sets the pod's agent answering in the member's own channel; a
    retried job finds the turn it started, and a retried answer is one line,
    in the thread of the line it answers, said for the member."""
    await scenario.create_org_with_pod(name_prefix="In-app")
    pod_id, owner = UUID(scenario.pod_id), UUID(scenario.owner_user["id"])
    said = await _member_says(pod_id, owner, "What is on today?")

    assert await _drain(WorkClass.INTERACTIVE) >= 2  # fan-out, then the answer
    conversation_id = await _answering_conversation(said.id)
    # The job again, as a retry after a lost lease would run it.
    again = await answer_line(_uows(), message_id=said.id, agent_id=pod_id)
    assert again == conversation_id

    [run] = await runs(conversation_id)
    asked = [
        message
        for message in await messages(conversation_id)
        if message.role is MessageRole.USER
    ]
    assert [message.text for message in asked] == ["What is on today?"]
    assert asked[0].metadata["client_message_id"] == turn_client_id(said.id)

    await _finish_with(run.id, "Two meetings.")
    finished = _finished(run, pod_id=pod_id, user_id=owner)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await facts.emit(uow, finished)
        await uow.commit()
    await _drain(WorkClass.EVENTS)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        # Finished twice -- a redelivered fact after the first item ran.
        await facts.emit(uow, finished)
        await uow.commit()
    await _drain(WorkClass.EVENTS)

    [answer] = await _lines_of_run(run.id)
    assert answer.body == "Two meetings."
    assert answer.sender_kind == SenderKind.AGENT.value
    assert answer.on_behalf_user_id == owner
    assert answer.conversation_id == conversation_id
    # A member's own channel reads as a chat: the answer follows the line it
    # answers rather than opening a thread under it.
    assert answer.thread_root_id is None
    assert answer.reply_to_id == said.id
    assert answer.channel_id == said.channel_id


async def test_the_next_line_continues_the_same_conversation(scenario):
    await scenario.create_org_with_pod(name_prefix="In-app")
    pod_id, owner = UUID(scenario.pod_id), UUID(scenario.owner_user["id"])
    first = await _member_says(pod_id, owner, "Hello")
    await _drain(WorkClass.INTERACTIVE)
    second = await _member_says(pod_id, owner, "And tomorrow?")
    await _drain(WorkClass.INTERACTIVE)

    assert await _answering_conversation(second.id) == await _answering_conversation(
        first.id
    )


async def test_an_agent_that_is_not_the_pods_answers_nothing(scenario):
    other = await scenario.create_org_with_pod(name_prefix="Elsewhere")
    elsewhere = UUID(other.pod_id)
    await scenario.create_org_with_pod(name_prefix="In-app")
    pod_id, owner = UUID(scenario.pod_id), UUID(scenario.owner_user["id"])
    said = await _member_says(pod_id, owner, "Hello")

    assert await answer_line(_uows(), message_id=said.id, agent_id=elsewhere) is None


# -- a run's questions, as lines waiting on the member ----------------------------


TWO_QUESTIONS = {
    "questions": [
        {"question": "Which size?", "header": "size", "options": [{"label": "S"}]},
        {
            "question": "Which colour?",
            "header": "colour",
            "options": [{"label": "Red"}],
        },
    ]
}


async def _paused(seeded: Seeded, *, tool_name: str, tool_args: dict) -> str:
    run_id = await running_run(seeded)
    call = await pausing_call(seeded, run_id, tool_name=tool_name, tool_args=tool_args)
    await finish(run_id, conversation_status=ConversationStatus.WAITING)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await sync_pause_questions(uow, seeded.conversation_id)
        await uow.commit()
    return call


async def _question(seeded: Seeded, call: str):
    async with create_uow_from_session_maker(async_session_maker) as uow:
        found = await channel_questions.question_by_ref(
            uow, pod_id=seeded.pod_id, ref=tool_ref(seeded.conversation_id, call)
        )
    assert found is not None
    return found


async def test_an_ask_waits_on_the_member_and_is_answered_field_by_field(scenario):
    seeded = await seeded_conversation(scenario)
    call = await _paused(seeded, tool_name="ask_user", tool_args=TWO_QUESTIONS)
    asked = await _question(seeded, call)
    assert asked.kind is QuestionKind.ASK
    assert asked.awaits == Principal.member(seeded.user_id)
    assert asked.expires_at is not None
    async with create_uow_from_session_maker(async_session_maker) as uow:
        line = await line_by_id(uow, asked.message_id)
        direct = await direct_channel(uow, pod_id=seeded.pod_id, user_id=seeded.user_id)
    # A conversation in no channel asks in the member's own.
    assert line is not None and line.channel_id == direct.id
    assert "Which size?" in line.text and "Which colour?" in line.text

    with pytest.raises(AnswerRefused) as refused:
        await channel_questions.answer(
            _uows(),
            message_id=asked.message_id,
            by=Principal.member(seeded.user_id),
            given={"choice": "S"},
        )
    assert refused.value.status_code == 422
    assert (await _question(seeded, call)).state is QuestionState.OPEN

    with suppress_agent_run_enqueue():
        answered = await channel_questions.answer(
            _uows(),
            message_id=asked.message_id,
            by=Principal.member(seeded.user_id),
            given={"answers": {"size": "S", "colour": "Red"}},
        )
    assert answered.state is QuestionState.ANSWERED
    assert answered.answer == {"answers": {"size": "S", "colour": "Red"}}


async def test_an_approval_is_a_persons_tap_never_an_agents(scenario):
    seeded = await seeded_conversation(scenario)
    call = await _paused(
        seeded,
        tool_name="request_approval",
        tool_args={"title": "Delete the table?", "tool_name": "pod_query"},
    )
    asked = await _question(seeded, call)
    assert asked.kind is QuestionKind.APPROVAL

    with pytest.raises(AnswerRefused) as refused:
        await channel_questions.answer(
            _uows(),
            message_id=asked.message_id,
            by=Principal.member(seeded.user_id),
            given={"decision": "APPROVE_ONCE"},
            through_agent=True,
        )
    assert refused.value.status_code == 403
    assert (await _question(seeded, call)).state is QuestionState.OPEN


async def test_a_line_answered_after_the_conversation_decided_shows_that_decision(
    scenario,
):
    """Denied in the conversation first; a tap on the line arriving after it
    records what took effect -- the denial -- not what the tap asked for."""
    seeded = await seeded_conversation(scenario)
    call = await _paused(
        seeded,
        tool_name="request_approval",
        tool_args={"title": "Send the invoice?", "tool_name": "pod_query"},
    )
    asked = await _question(seeded, call)
    with suppress_agent_run_enqueue():
        async with owner_uow(seeded) as uow:
            await build_conversation_service(uow).resolve_user_approval(
                conversation_id=seeded.conversation_id,
                approval_id=call,
                user_id=seeded.user_id,
                pod_id=seeded.pod_id,
                decision=AgentRunApprovalDecision.DENY,
            )
            await uow.commit()
        answered = await channel_questions.answer(
            _uows(),
            message_id=asked.message_id,
            by=Principal.member(seeded.user_id),
            given={"decision": "APPROVE_ONCE"},
        )
    assert answered.answer == {"decision": "DENY"}


async def test_a_question_decided_elsewhere_closes_with_that_decision(scenario):
    seeded = await seeded_conversation(scenario)
    call = await _paused(seeded, tool_name="ask_user", tool_args=TWO_QUESTIONS)
    with suppress_agent_run_enqueue():
        async with owner_uow(seeded) as uow:
            await build_conversation_service(uow).resolve_user_approval(
                conversation_id=seeded.conversation_id,
                approval_id=call,
                user_id=seeded.user_id,
                pod_id=seeded.pod_id,
                decision=AgentRunApprovalDecision.APPROVE_ONCE,
                response={"answers": {"size": "M", "colour": "Blue"}},
            )
            await uow.commit()
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await sync_pause_questions(uow, seeded.conversation_id)
        await uow.commit()

    closed = await _question(seeded, call)
    assert closed.state is QuestionState.ANSWERED
    assert closed.answer == {"answers": {"size": "M", "colour": "Blue"}}
    assert closed.answered_by == Principal.member(seeded.user_id)


async def test_a_pause_is_asked_once_however_often_its_run_ends(scenario):
    seeded = await seeded_conversation(scenario)
    call = await _paused(seeded, tool_name="ask_user", tool_args=TWO_QUESTIONS)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await sync_pause_questions(uow, seeded.conversation_id)
        await uow.commit()
    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count()).where(
                ChannelMessageModel.client_message_id
                == tool_ref(seeded.conversation_id, call)
            )
        )
    assert count == 1
