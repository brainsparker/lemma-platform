"""Questions agents put to people, kept in step with the conversations that asked.

* ``agent.pause_questions`` -- subscribed to ``AgentRunFinished`` for a run of
  a member's conversation: one that paused asks its new questions as lines
  waiting on the person; any run's end -- the resume after an answer, a stop,
  a failure -- closes the lines of questions no longer waiting, with the
  decision taken elsewhere or cancelled (``services.pause_questions``). FAST.
* ``agent.asked_question_closed`` -- subscribed to channels'
  ``ChannelQuestionClosed`` for a question a conversation put to a member
  (``msg:``): once nothing of its batch is still open, the asking conversation
  gets its turn back (``agent.replies_arrived``, one per batch). FAST.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    pod_namespace,
    registry as default_registry,
)
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.domain.asked_questions import asking_conversation
from app.modules.agent.events.turn_payloads import RepliesArrived
from app.modules.agent.events.work_kinds import (
    AGENT_ASKED_QUESTION_CLOSED,
    AGENT_PAUSE_QUESTIONS,
    AGENT_REPLIES_ARRIVED,
    asked_question_closed_key,
    pause_questions_key,
    replies_key,
)
from app.modules.agent.services.pause_questions import sync_pause_questions
from app.modules.channels.contracts.facts import ChannelQuestionClosed
from app.modules.channels.contracts.questions import open_in_batch


class PauseQuestionsPayload(BaseModel):
    """The fields of ``AgentRunFinished`` this needs; the rest are ignored."""

    model_config = ConfigDict(extra="ignore")

    run_id: UUID
    conversation_id: UUID


class QuestionClosedPayload(BaseModel):
    """The fields of ``ChannelQuestionClosed`` this needs; the rest are ignored."""

    model_config = ConfigDict(extra="ignore")

    pod_id: UUID
    message_id: UUID
    ref: str
    batch_id: str | None = None


def concerns_a_person(fact: AgentRunFinished) -> bool:
    """A run of a conversation somebody could have been asked something in:
    a member's, which a person -- not a pod, not a parent agent -- answers."""
    return fact.user_id is not None and fact.parent_kind != "agent"


def closes_an_asked_batch(fact: ChannelQuestionClosed) -> bool:
    return fact.batch_id is not None and asking_conversation(fact.ref) is not None


async def sync_questions(ctx: FastContext, payload: PauseQuestionsPayload) -> None:
    await sync_pause_questions(ctx.uow, payload.conversation_id)


async def settle_batch(ctx: FastContext, payload: QuestionClosedPayload) -> None:
    conversation_id = asking_conversation(payload.ref)
    if conversation_id is None or payload.batch_id is None:
        return
    if await open_in_batch(ctx.uow, pod_id=payload.pod_id, batch_id=payload.batch_id):
        return
    await ctx.work.add(
        AGENT_REPLIES_ARRIVED,
        pod_namespace(payload.pod_id),
        RepliesArrived(
            conversation_id=conversation_id,
            pod_id=payload.pod_id,
            batch_id=payload.batch_id,
        ),
        dedup_key=replies_key(payload.batch_id),
    )


def register_channel_question_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=AGENT_PAUSE_QUESTIONS,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=PauseQuestionsPayload,
    )(sync_questions)
    registry.handler(
        kind=AGENT_ASKED_QUESTION_CLOSED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=QuestionClosedPayload,
    )(settle_batch)


register_channel_question_work(default_registry)
facts.subscribe(
    AgentRunFinished,
    kind=AGENT_PAUSE_QUESTIONS,
    cls=WorkClass.EVENTS,
    when=concerns_a_person,
    dedup=lambda fact: pause_questions_key(fact.run_id),
)
facts.subscribe(
    ChannelQuestionClosed,
    kind=AGENT_ASKED_QUESTION_CLOSED,
    cls=WorkClass.EVENTS,
    when=closes_an_asked_batch,
    dedup=lambda fact: asked_question_closed_key(fact.message_id),
)


__all__ = [
    "closes_an_asked_batch",
    "concerns_a_person",
    "register_channel_question_work",
    "settle_batch",
    "sync_questions",
]
