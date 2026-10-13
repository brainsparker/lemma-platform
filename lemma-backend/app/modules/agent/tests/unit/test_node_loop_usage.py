"""A run's usage reaches the event queue even when the consumer is behind.

The queue between the graph driver and the consumer is bounded, and a consumer
that fell behind a long streamed answer leaves it full. Usage used to be put
with ``put_nowait``, which raised ``QueueFull`` there -- after marking usage as
emitted, so nothing retried it and a finished answer was reported as a failure.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic_ai.usage import RunUsage

from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import AgentEventType
from app.modules.agent.infrastructure.harnesses.pydantic_ai_node_loop import NodeLoop

pytestmark = pytest.mark.asyncio


def _loop_over(queue: asyncio.Queue) -> NodeLoop:
    loop = NodeLoop(
        run_context=lambda _history: None,
        queue=queue,
        streamer=None,
        options=HarnessOptions(model_name="test-model"),
        agent_run_id=uuid4(),
        conversation_id=uuid4(),
        final_output_message=lambda **_kwargs: None,
    )
    # The attempt in progress, as `drive_once` publishes it.
    loop._run = SimpleNamespace(usage=RunUsage(input_tokens=7, output_tokens=3))
    return loop


def _full_queue() -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    queue.put_nowait(("event", "token-1"))
    queue.put_nowait(("event", "token-2"))
    return queue


async def test_usage_waits_for_a_stalled_consumer_instead_of_failing_the_run() -> None:
    queue = _full_queue()
    loop = _loop_over(queue)

    emitting = asyncio.ensure_future(loop.emit_usage())
    await asyncio.sleep(0)
    assert not emitting.done(), "a full queue must make usage wait, not raise"

    # The consumer catches up.
    assert queue.get_nowait() == ("event", "token-1")
    await asyncio.wait_for(emitting, timeout=5)

    assert queue.get_nowait() == ("event", "token-2")
    _label, event = queue.get_nowait()
    assert event.type is AgentEventType.USAGE
    assert event.data.input_tokens == 7
    assert event.data.output_tokens == 3


async def test_a_usage_put_cancelled_while_waiting_can_still_be_emitted() -> None:
    """Marked emitted only once it is on the queue: an attempt that never got
    there must not stop the fallback from reporting it."""
    queue = _full_queue()
    loop = _loop_over(queue)

    emitting = asyncio.ensure_future(loop.emit_usage())
    await asyncio.sleep(0)
    emitting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await emitting

    queue.get_nowait()
    queue.get_nowait()
    await asyncio.wait_for(loop.emit_usage(), timeout=5)

    _label, event = queue.get_nowait()
    assert event.type is AgentEventType.USAGE
    assert queue.empty()
    # And only once: the call after it is a no-op.
    await loop.emit_usage()
    assert queue.empty()
