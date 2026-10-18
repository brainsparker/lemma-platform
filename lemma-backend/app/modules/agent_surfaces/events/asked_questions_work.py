"""``surface.asked_questions``: a question an agent asked somebody closed.

Subscribed to the channels module's ``ChannelQuestionClosed`` for messages --
a question one conversation put to a member (``msg:`` refs). Once none of that
conversation's questions is still open, it is owed no more answers, and
``AskedQuestionsSettled`` gives it its turn back. FAST: main-database
statements only, committed with the item.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent_surfaces.contracts.question_facts import AskedQuestionsSettled
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_ASKED_QUESTIONS,
    asked_questions_key,
)
from app.modules.agent_surfaces.services.direct_messages import (
    MESSAGE_REF,
    asking_conversation,
)
from app.modules.channels.contracts.facts import ChannelQuestionClosed
from app.modules.channels.contracts.questions import open_questions_under


class QuestionClosedPayload(BaseModel):
    """The fields of ``ChannelQuestionClosed`` this needs; the rest are ignored."""

    pod_id: UUID
    message_id: UUID
    ref: str | None = None


def closes_a_message(fact: ChannelQuestionClosed) -> bool:
    return bool(fact.ref) and asking_conversation(fact.ref or "") is not None


async def settle_if_answered(ctx: FastContext, payload: QuestionClosedPayload) -> None:
    conversation_id = asking_conversation(payload.ref or "")
    if conversation_id is None:
        return
    still_open = await open_questions_under(
        ctx.uow, prefix=f"{MESSAGE_REF}:{conversation_id}:"
    )
    if still_open:
        return
    await facts.emit(
        ctx.uow,
        AskedQuestionsSettled(conversation_id=conversation_id, pod_id=payload.pod_id),
    )


def register_asked_questions_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACE_ASKED_QUESTIONS,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=QuestionClosedPayload,
    )(settle_if_answered)


register_asked_questions_work(default_registry)
facts.subscribe(
    ChannelQuestionClosed,
    kind=SURFACE_ASKED_QUESTIONS,
    cls=WorkClass.EVENTS,
    when=closes_a_message,
    dedup=lambda fact: asked_questions_key(fact.message_id),
)
