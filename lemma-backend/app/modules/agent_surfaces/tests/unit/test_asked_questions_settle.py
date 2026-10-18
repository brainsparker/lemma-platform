"""Saying that an asking conversation is owed nothing further.

The behaviour is a *negative* one as much as a positive one: an agent that
messaged four people must be woken once, not four times, because acting on it
replays the whole conversation. So the interesting assertions here are the
ones that check nothing was announced.

The announcement is made by the ``surface.asked_questions`` work item, which
runs in the transaction that closed a question -- answered in the app, on a
platform, or cancelled -- so no answering path has to remember to wake the
asker. Here over its two database reads: the open questions under the asking
conversation, and the fact it emits.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.modules.agent_surfaces.contracts.question_facts import AskedQuestionsSettled
from app.modules.agent_surfaces.events import asked_questions_work
from app.modules.agent_surfaces.events.asked_questions_work import (
    QuestionClosedPayload,
    closes_a_message,
    settle_if_answered,
)
from app.modules.agent_surfaces.infrastructure.adapters.workflow_notifications import (
    form_ref,
)
from app.modules.agent_surfaces.services.direct_messages import message_ref
from app.modules.channels.contracts import InteractionState
from app.modules.channels.contracts.facts import ChannelQuestionClosed

pytestmark = pytest.mark.unit


class _Questions:
    """The questions still open, by ref, and what was announced."""

    def __init__(self, *refs: str) -> None:
        self.open = set(refs)
        self.asked_prefixes: list[str] = []
        self.announced: list[AskedQuestionsSettled] = []

    async def open_questions_under(self, _uow, *, prefix: str):
        self.asked_prefixes.append(prefix)
        return [ref for ref in self.open if ref.startswith(prefix)]

    async def emit(self, _uow, fact) -> None:
        self.announced.append(fact)

    async def close(self, ref: str, *, pod_id: UUID) -> None:
        """Close one question, then run the work item its closing enqueues."""
        self.open.discard(ref)
        fact = ChannelQuestionClosed(
            pod_id=pod_id,
            channel_id=uuid4(),
            message_id=uuid4(),
            ref=ref,
            state=InteractionState.ANSWERED,
        )
        if not closes_a_message(fact):
            return
        await settle_if_answered(
            SimpleNamespace(uow=SimpleNamespace()),
            QuestionClosedPayload.model_validate(fact.model_dump()),
        )


@pytest.fixture
def questions(monkeypatch) -> _Questions:
    questions = _Questions()
    monkeypatch.setattr(
        asked_questions_work, "open_questions_under", questions.open_questions_under
    )
    monkeypatch.setattr(asked_questions_work.facts, "emit", questions.emit)
    return questions


@pytest.mark.asyncio
async def test_the_last_answer_announces_that_the_conversation_is_settled(questions):
    conversation_id, pod_id = uuid4(), uuid4()
    ref = message_ref(conversation_id, "a")
    questions.open.add(ref)

    await questions.close(ref, pod_id=pod_id)

    [event] = questions.announced
    assert (event.conversation_id, event.pod_id) == (conversation_id, pod_id)


@pytest.mark.asyncio
async def test_an_answer_with_others_still_open_announces_nothing(questions):
    """The whole point of waiting for the last one.

    Announcing here would replay the entire conversation so the agent could
    learn that two people still owe it an answer, and stop again.
    """
    conversation_id, pod_id = uuid4(), uuid4()
    first, second, third = (message_ref(conversation_id, k) for k in "abc")
    questions.open.update({first, second, third})

    await questions.close(first, pod_id=pod_id)
    await questions.close(second, pod_id=pod_id)
    assert questions.announced == []

    await questions.close(third, pod_id=pod_id)
    assert [e.conversation_id for e in questions.announced] == [conversation_id]


@pytest.mark.asyncio
async def test_another_conversations_open_question_does_not_hold_this_one(questions):
    """The count is per asking conversation: the ref's prefix carries it."""
    mine, theirs, pod_id = uuid4(), uuid4(), uuid4()
    ref = message_ref(mine, "a")
    questions.open.update({ref, message_ref(theirs, "b")})

    await questions.close(ref, pod_id=pod_id)

    assert [e.conversation_id for e in questions.announced] == [mine]
    assert questions.asked_prefixes == [f"msg:{mine}:"]


@pytest.mark.asyncio
async def test_a_workflow_form_answer_announces_nothing(questions):
    """A form is owed to its workflow run, which the engine resumes itself.

    Announcing from here would be a second resume path for an execution that
    already has one -- so the item is never even enqueued for a form's ref.
    """
    ref = form_ref(uuid4(), "approve")

    await questions.close(ref, pod_id=uuid4())

    assert questions.announced == []
    assert questions.asked_prefixes == []


@pytest.mark.asyncio
async def test_a_message_with_no_asking_conversation_announces_nothing(questions):
    """A workflow or the pod asking: there is nobody to wake."""
    ref = message_ref(None, "a")

    await questions.close(ref, pod_id=uuid4())

    assert questions.announced == []
    assert questions.asked_prefixes == []


@pytest.mark.asyncio
async def test_a_closed_question_with_no_ref_is_not_a_message():
    """Without a ref there is no asking conversation to name, so none to wake."""
    fact = ChannelQuestionClosed(
        pod_id=uuid4(),
        channel_id=uuid4(),
        message_id=uuid4(),
        ref=None,
        state=InteractionState.CANCELLED,
    )

    assert closes_a_message(fact) is False
