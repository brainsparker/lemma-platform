"""The other half of ``channel_post``: answering a question put to the person
this conversation is with.

Runs in the *recipient's* conversation, as the recipient: their agent answers
under their authority what was asked of them -- a member's message, a
workflow's form -- when they answer in words rather than on the line itself.
The answer goes where answering on the line would send it: back to the asking
conversation, or into the workflow, which validates it.
"""

from __future__ import annotations

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.outsiders import run_audience
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.models import (
    AnswerQuestionRequest,
    AnswerQuestionResponse,
)
from app.modules.channels.contracts import questions as channel_questions
from app.modules.channels.contracts.questions import AnswerRefused

ANSWER_TOOL_NAME = "answer_question"


async def answer_question(
    ctx: RunContext[BaseAgentContext], request: AnswerQuestionRequest
) -> AnswerQuestionResponse:
    """Record their answer to a question waiting on them.

    Only once they have actually answered -- not when they say they will get to
    it. For a form, pass the form's fields; a wrong or missing field is
    refused, and you can ask them again. Never answer for them, or with detail
    they did not give you.
    """
    deps = ctx.deps
    if deps.pod_id is None or run_audience(deps).answers_outsiders:
        return AnswerQuestionResponse(
            success=False, error="Only a member's questions are answered here."
        )
    try:
        await channel_questions.answer_as_member(
            pod_id=deps.pod_id,
            message_id=request.question_id,
            user_id=deps.user_id,
            answer=request.answer,
        )
    except AnswerRefused as refused:
        return AnswerQuestionResponse(success=False, error=str(refused))
    return AnswerQuestionResponse(
        success=True,
        message=(
            "Recorded. Keep your reply to them short: confirm you passed it on, "
            "and stop."
        ),
    )


answer_toolset = FunctionToolset[BaseAgentContext](tools=[answer_question])
