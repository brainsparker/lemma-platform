"""What the asking conversation does when the people it messaged answer.

The news arrives as input, and input normally starts a turn. Three states make
that wrong, and each is a different answer:

- nothing is suspended: the news is the next turn;
- a `wait_for` is holding the turn: resolving that wait is the way back in, and
  a message posted past it would leave the wait armed to fire again;
- the owner was asked something and has not answered: the news is not their
  answer, and starting a turn would deny the question on their behalf.

Against real repositories: these replaced tests that patched six collaborators
inside the service and asserted the arguments of a mocked `start`, which could
not see any of the three.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.pausing_tools import (
    USER_PAUSING_TOOL_NAMES,
    WAIT_TOOL_NAME,
)
from app.modules.agent.domain.value_objects import (
    ConversationStatus,
    MessageKind,
    MessageRole,
)
from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitStatus,
    AgentWaitType,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.wait_repository import (
    AgentConversationWaitRepository,
)
from app.modules.agent.services.message_reply_service import (
    REPLY_SOURCE,
    MessageReplyService,
)
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    finish,
    messages,
    pausing_call,
    running_run,
    runs,
    seeded_conversation,
)

pytestmark = [pytest.mark.e2e]


async def _deliver(seeded: Seeded) -> bool:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        return await MessageReplyService(uow).deliver(
            conversation_id=seeded.conversation_id, pod_id=seeded.pod_id
        )


def _news(found):
    return [
        message
        for message in found
        if message.role is MessageRole.USER
        and (message.metadata or {}).get("source") == REPLY_SOURCE
    ]


async def test_a_finished_conversation_gets_the_news_as_its_next_turn(scenario):
    seeded = await seeded_conversation(scenario)

    assert await _deliver(seeded) is True

    (started,) = await runs(seeded.conversation_id)
    (news,) = _news(await messages(seeded.conversation_id))
    assert news.agent_run_id == started.id
    assert "check_messages" in (news.text or "")


async def test_an_open_question_is_not_answered_by_the_news(scenario):
    """The owner's approval stays pending; the news waits behind it."""
    seeded = await seeded_conversation(scenario)
    asking = await running_run(seeded)
    approval_id = await pausing_call(
        seeded,
        asking,
        tool_name="request_approval",
        tool_args={"tool_name": "exec_command", "args": {"cmd": "deploy"}},
    )
    await finish(asking, conversation_status=ConversationStatus.WAITING)

    assert await _deliver(seeded) is True

    assert [run.id for run in await runs(seeded.conversation_id)] == [asking]
    async with create_uow_from_session_maker(async_session_maker) as uow:
        open_questions = await ConversationRepository(
            uow
        ).pausing_calls_awaiting_a_decision(
            conversation_id=seeded.conversation_id,
            pausing_tool_names=USER_PAUSING_TOOL_NAMES,
        )
    assert [call.tool_call_id for call in open_questions] == [approval_id]
    found = await messages(seeded.conversation_id)
    assert not [m for m in found if m.kind is MessageKind.TOOL_RETURN]
    (news,) = _news(found)
    assert news.agent_run_id == asking
    assert news.metadata["during_active_run"] is True


async def test_a_sleeping_conversation_is_woken_rather_than_written_past(scenario):
    seeded = await seeded_conversation(scenario)
    sleeping = await running_run(seeded)
    tool_call_id = await pausing_call(
        seeded, sleeping, tool_name=WAIT_TOOL_NAME, tool_args={"reason": "replies"}
    )
    await finish(sleeping, conversation_status=ConversationStatus.WAITING)
    now = datetime.now(timezone.utc)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        wait = await AgentConversationWaitRepository(uow).create(
            AgentConversationWaitEntity(
                conversation_id=seeded.conversation_id,
                agent_run_id=sleeping,
                pod_id=seeded.pod_id,
                tool_call_id=tool_call_id,
                wait_type=AgentWaitType.TIME,
                external_ref=str(uuid4()),
                scheduled_at=now + timedelta(hours=1),
                spec={"started_at": now.isoformat(), "user_id": str(seeded.user_id)},
            )
        )
        await uow.commit()

    assert await _deliver(seeded) is True

    found = await messages(seeded.conversation_id)
    assert _news(found) == [], "a message was posted past a live wait"
    (woke,) = [m for m in found if m.kind is MessageKind.TOOL_RETURN]
    assert woke.tool_call_id == tool_call_id
    assert woke.tool_result["woke_because"] == "ANSWERED"
    async with create_uow_from_session_maker(async_session_maker) as uow:
        reloaded = await AgentConversationWaitRepository(uow).get(wait.id)
    assert reloaded is not None and reloaded.status is AgentWaitStatus.COMPLETED


async def test_a_conversation_that_is_gone_is_not_an_error(scenario):
    """Somebody answered a question whose asker was deleted; their answer is
    still on its row, and raising would push that deletion back at them."""
    seeded = await seeded_conversation(scenario)
    gone = Seeded(
        pod_id=seeded.pod_id,
        user_id=seeded.user_id,
        agent_id=seeded.agent_id,
        agent_name=seeded.agent_name,
        conversation_id=uuid4(),
    )

    assert await _deliver(gone) is False
