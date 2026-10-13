"""One failure, one explanation, whichever layer caught it.

The harness and the runner each turned an exception into a sentence, written
apart and drifting: the same exception read differently depending on where it
surfaced, and a response cut off by the output cap was reported as a tool
failing repeatedly, sending people to check an agent configuration that was
fine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from pydantic_ai.exceptions import (
    IncompleteToolCall,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingCalls,
    DeltaThinkingPart,
    FunctionModel,
)

from app.core.domain.errors import DomainError
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import AgentEventType
from app.modules.agent.infrastructure.harnesses.provider_error_log import (
    user_facing_error_message,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai import PydanticAIHarness
from app.modules.agent.infrastructure.harnesses.pydantic_ai_retry import (
    HarnessDriverCancelled,
)
from app.modules.agent.infrastructure.transport_errors import (
    ModelResponseBudgetExceeded,
)
from app.modules.agent.services.run_finalizer import run_failure_message
from app.modules.usage.contracts import UsageLimitExceededError

pytestmark = pytest.mark.unit

_REQUEST = httpx.Request("POST", "https://provider.example/v1/chat")
_OUTPUT_ROOM = "ran out of room to answer"


def _http(status: int, body: object = None) -> ModelHTTPError:
    return ModelHTTPError(status, "private-model", body=body)


_FAILURES: list[tuple[str, Exception]] = [
    ("rate limited", _http(429)),
    ("billing", _http(402)),
    ("provider down", _http(503)),
    ("key rejected", _http(401)),
    ("no such model", _http(404)),
    (
        "context window",
        _http(400, {"error": {"code": "context_length_exceeded"}}),
    ),
    ("other 4xx", _http(418)),
    ("dropped connection", httpx.ReadError("connection reset")),
    (
        "over the time budget",
        ModelResponseBudgetExceeded(
            "x", request=_REQUEST, elapsed_seconds=901.0, budget_seconds=900.0
        ),
    ),
    ("cut off mid tool call", IncompleteToolCall("Model token limit (10) exceeded")),
    (
        "thought until the cap",
        UnexpectedModelBehavior(
            "Model token limit (10) exceeded before any response was generated."
        ),
    ),
    ("invalid tool arguments", UnexpectedModelBehavior("Tool exceeded max retries")),
    ("pydantic-ai usage limit", UsageLimitExceeded("request_limit of 5")),
    ("driver cancelled", HarnessDriverCancelled("cancelled")),
    ("workspace allowance", UsageLimitExceededError()),
    ("domain error", DomainError("A sentence written for the reader.", code="X")),
    ("anything else", ValueError("private detail")),
]


@pytest.mark.parametrize(
    "exc", [exc for _, exc in _FAILURES], ids=[name for name, _ in _FAILURES]
)
def test_the_harness_and_the_runner_say_the_same_thing(exc: Exception) -> None:
    assert user_facing_error_message(exc) == run_failure_message(exc)


@pytest.mark.parametrize(
    "exc",
    [
        IncompleteToolCall("Model token limit (10) exceeded while generating"),
        UnexpectedModelBehavior(
            "Model token limit (10) exceeded before any response was generated."
        ),
    ],
)
def test_running_out_of_output_room_is_not_blamed_on_a_tool(exc: Exception) -> None:
    message = run_failure_message(exc)

    assert _OUTPUT_ROOM in message
    assert "tool" not in message.lower()


def test_a_tool_that_kept_failing_is_still_named_as_one() -> None:
    message = run_failure_message(UnexpectedModelBehavior("Tool exceeded max retries"))

    assert "A tool failed repeatedly" in message


async def test_a_model_that_only_thought_until_the_cap_says_it_ran_out_of_room() -> (
    None
):
    """A reasoning model that spends its whole output budget thinking."""

    async def think(messages, info: AgentInfo) -> AsyncIterator[DeltaThinkingCalls]:
        del messages, info
        yield {0: DeltaThinkingPart(content="Let me consider every angle...")}

    class _CutOffAtTheCap(FunctionModel):
        """Streams only thinking, then stops the way a provider does at the cap."""

        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            async with super().request_stream(*args, **kwargs) as response:
                response.finish_reason = "length"
                yield response

    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    events = [
        event
        async for event in PydanticAIHarness(
            model=_CutOffAtTheCap(stream_function=think)
        ).run(
            agent=Agent(
                pod_id=pod_id,
                user_id=conversation.user_id,
                name="assistant",
                instruction="",
            ),
            conversation=conversation,
            messages=[],
            ctx=AgentContext(
                user_id=conversation.user_id,
                pod_id=pod_id,
                conversation_id=conversation.id,
            ),
            options=HarnessOptions(
                model_name="test-model", history_summarization_enabled=False
            ),
            agent_run_id=uuid4(),
        )
    ]

    (error,) = [e for e in events if e.type is AgentEventType.ERROR]
    assert _OUTPUT_ROOM in str(error.data)
    assert "tool" not in str(error.data).lower()
