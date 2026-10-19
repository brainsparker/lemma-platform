"""The messaging tools over the channels module, against Postgres.

An agent asking members things with ``channel_post``, reading what came back
with ``channel_read(asked_by_me=True)``, being woken once when the last of a
run's questions closes, and -- on the other side -- the member's own agent
being told what waits on them and answering it, never an approval.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.agent.capabilities.open_questions import open_question_text
from app.modules.agent.domain.value_objects import MessageRole
from app.modules.agent.events.channel_question_work import (
    register_channel_question_work,
)
from app.modules.agent.events.run_followup_work import register_run_followup_work
from app.modules.agent.services.message_reply_service import REPLY_SOURCE
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    messages,
    running_run,
    seeded_conversation,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.answer import answer_question
from app.modules.agent.tools.messaging.models import (
    AnswerQuestionRequest,
    ChannelListRequest,
    ChannelPostRequest,
    ChannelReadRequest,
    ChannelSearchRequest,
)
from app.modules.agent.tools.messaging.pydantic_adapter import (
    channel_list,
    channel_post,
    channel_read,
    channel_search,
)
from app.modules.channels.contracts import (
    NewLine,
    NewQuestion,
    Principal,
    QuestionKind,
    QuestionState,
    Sender,
    direct_channel,
    post,
)
from app.modules.channels.contracts import questions as channel_questions

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


def _ctx(
    seeded: Seeded,
    *,
    user_id: UUID | None = None,
    run_id: UUID | None = None,
    call: str | None = None,
    conversation_id: UUID | None = None,
) -> RunContext[BaseAgentContext]:
    return RunContext(
        deps=BaseAgentContext(
            user_id=user_id or seeded.user_id,
            pod_id=seeded.pod_id,
            conversation_id=conversation_id or seeded.conversation_id,
            agent_run_id=run_id,
            workload_id=seeded.agent_id,
            agent_name=seeded.agent_name,
        ),
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
        tool_call_id=call,
    )


async def _colleague(scenario) -> dict[str, str]:
    colleague = await scenario.create_user("asked-colleague")
    await scenario.add_user_to_pod(user=colleague, role="POD_EDITOR")
    return colleague


async def _ask(seeded: Seeded, run_id: UUID, to: str, text: str, call: str):
    sent = await channel_post(
        _ctx(seeded, run_id=run_id, call=call),
        ChannelPostRequest(text=text, to=to, wants_answer=True),
    )
    assert sent.success, sent.error
    return sent


async def _drain_answers() -> int:
    registry = WorkRegistry()
    register_channel_question_work(registry)
    register_run_followup_work(registry)
    await make_due(async_session_maker)
    with suppress_agent_run_enqueue():
        return await run_until_idle(
            async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
        )


async def _news(conversation_id: UUID) -> list:
    return [
        message
        for message in await messages(conversation_id)
        if message.role is MessageRole.USER
        and (message.metadata or {}).get("source") == REPLY_SOURCE
    ]


async def test_a_runs_questions_wake_the_asker_once_the_last_is_answered(scenario):
    seeded = await seeded_conversation(scenario)
    colleague = await _colleague(scenario)
    colleague_id = UUID(colleague["id"])
    run_id = await running_run(seeded)
    first = await _ask(seeded, run_id, colleague["email"], "Is the 14th ok?", "q1")
    second = await _ask(seeded, run_id, colleague["email"], "And the venue?", "q2")
    # A retried call is the same line and the same question.
    again = await _ask(seeded, run_id, colleague["email"], "Is the 14th ok?", "q1")
    assert again.message_id == first.message_id
    async with create_uow_from_session_maker(async_session_maker) as uow:
        direct = await direct_channel(uow, pod_id=seeded.pod_id, user_id=colleague_id)
    assert first.channel_id == direct.id

    # Newest first, both waiting.
    read = await channel_read(_ctx(seeded), ChannelReadRequest(asked_by_me=True))
    assert [line.id for line in read.lines] == [second.message_id, first.message_id]
    assert {line.question_state for line in read.lines} == {"OPEN"}
    assert {line.awaits for line in read.lines} == {str(colleague_id)}

    by = Principal.member(colleague_id)
    await channel_questions.answer(
        SessionUnitOfWorkFactory(async_session_maker),
        message_id=first.message_id,
        by=by,
        given={"text": "Yes"},
    )
    await _drain_answers()
    assert await _news(seeded.conversation_id) == [], "woken before the last answer"

    await channel_questions.answer(
        SessionUnitOfWorkFactory(async_session_maker),
        message_id=second.message_id,
        by=by,
        given={"text": "The barn"},
    )
    await _drain_answers()
    await _drain_answers()
    assert len(await _news(seeded.conversation_id)) == 1

    read = await channel_read(_ctx(seeded), ChannelReadRequest(asked_by_me=True))
    assert [line.answer for line in read.lines] == [
        {"text": "The barn"},
        {"text": "Yes"},
    ]


async def test_a_question_closed_twice_over_still_wakes_once(scenario):
    """Two closes whose items both find the batch settled: one wake-up."""
    seeded = await seeded_conversation(scenario)
    colleague = await _colleague(scenario)
    run_id = await running_run(seeded)
    first = await _ask(seeded, run_id, colleague["email"], "One?", "a")
    second = await _ask(seeded, run_id, colleague["email"], "Two?", "b")
    async with create_uow_from_session_maker(async_session_maker) as uow:
        for sent in (first, second):
            await channel_questions.close(
                uow, message_id=sent.message_id, state=QuestionState.CANCELLED
            )
        await uow.commit()
    await _drain_answers()

    assert len(await _news(seeded.conversation_id)) == 1


async def test_the_members_agent_sees_questions_not_approvals_and_answers_them(
    scenario,
):
    seeded = await seeded_conversation(scenario)
    colleague = await _colleague(scenario)
    colleague_id = UUID(colleague["id"])
    run_id = await running_run(seeded)
    asked = await _ask(seeded, run_id, colleague["email"], "Budget for Q4?", "b")
    async with create_uow_from_session_maker(async_session_maker) as uow:
        direct = await direct_channel(uow, pod_id=seeded.pod_id, user_id=colleague_id)
        approval = await post(
            uow,
            channel_id=direct.id,
            line=NewLine(
                sender=Sender.agent(
                    seeded.agent_id, on_behalf_of=Principal.member(colleague_id)
                ),
                text="May I delete the table?",
            ),
            question=NewQuestion(
                kind=QuestionKind.APPROVAL,
                ref=f"tool:{uuid4()}:call",
                question={"tool": "request_approval", "prompt": "Delete?"},
                awaits=Principal.member(colleague_id),
            ),
        )
        await uow.commit()
    assert approval is not None

    shown = await open_question_text(
        conversation_id=uuid4(),
        channel_id=direct.id,
        pod_id=seeded.pod_id,
        user_id=colleague_id,
    )
    assert shown is not None
    assert "Budget for Q4?" in shown and str(asked.message_id) in shown
    assert "delete the table" not in shown and str(approval.id) not in shown

    theirs = _ctx(seeded, user_id=colleague_id, conversation_id=uuid4())
    refused = await answer_question(
        theirs,
        AnswerQuestionRequest(
            question_id=approval.id, answer={"decision": "APPROVE_ONCE"}
        ),
    )
    assert refused.success is False
    recorded = await answer_question(
        theirs,
        AnswerQuestionRequest(question_id=asked.message_id, answer={"text": "40k"}),
    )
    assert recorded.success, recorded.error
    async with create_uow_from_session_maker(async_session_maker) as uow:
        approval_now = await channel_questions.question_of(uow, approval.id)
        asked_now = await channel_questions.question_of(uow, asked.message_id)
    assert approval_now is not None and approval_now.state is QuestionState.OPEN
    assert asked_now is not None and asked_now.answer == {"text": "40k"}


async def test_a_channel_is_listed_read_searched_and_written_as_its_member_sees_it(
    scenario,
):
    seeded = await seeded_conversation(scenario)
    colleague = await _colleague(scenario)
    colleague_id = UUID(colleague["id"])
    async with create_uow_from_session_maker(async_session_maker) as uow:
        theirs = await direct_channel(uow, pod_id=seeded.pod_id, user_id=colleague_id)
        await post(
            uow,
            channel_id=theirs.id,
            line=NewLine(sender=Sender.member(colleague_id), text="zebra crossing"),
        )
        mine = await direct_channel(uow, pod_id=seeded.pod_id, user_id=seeded.user_id)
        await uow.commit()

    listed = await channel_list(_ctx(seeded), ChannelListRequest())
    ids = {channel.id for channel in listed.channels}
    assert mine.id in ids and theirs.id not in ids

    refused = await channel_read(_ctx(seeded), ChannelReadRequest(channel_id=theirs.id))
    assert refused.success is False
    found = await channel_search(_ctx(seeded), ChannelSearchRequest(query="zebra"))
    assert found.lines == []
    written = await channel_post(
        _ctx(seeded), ChannelPostRequest(text="hi", channel_id=theirs.id)
    )
    assert written.success is False

    posted = await channel_post(
        _ctx(seeded, run_id=await running_run(seeded), call="note"),
        ChannelPostRequest(text="a note to myself", channel_id=mine.id),
    )
    assert posted.success, posted.error
    read = await channel_read(_ctx(seeded), ChannelReadRequest(channel_id=mine.id))
    assert [line.text for line in read.lines][-1] == "a note to myself"
