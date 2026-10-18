"""An agent asks a member something, and is brought back by their answer.

The whole round trip, with only the model's moves scripted:

1. A member asks the pod's agent, in their own channel in Lemma, to find out
   something from a colleague. The agent calls ``channel_post(to=...,
   wants_answer=True)``: a line in the colleague's own channel, waiting on
   them, carried to their Telegram chat with the pod's bot -- said on the
   asker's behalf. The agent ends its turn; nothing is held open.
2. The colleague answers in words, on Telegram. Their own agent there is told
   what is waiting on them, and records the answer with ``answer_question``.
3. That closes the last question the asking conversation was owed, so it gets
   its turn back (``AskedQuestionsSettled``), and ``channel_read(asked_by_me)``
   shows it what the colleague said.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.agent.events.run_followup_work import register_run_followup_work
from app.modules.agent.services.message_reply_service import REPLIES_ARRIVED
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.events.asked_questions_work import (
    register_asked_questions_work,
)
from app.modules.agent_surfaces.events.in_app_turn_work import (
    register_in_app_turn_work,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_surface,
    _messages_for_conversation,
    _seed_external_user,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    record_model_requests,
    run_scripted_agent_run,
    script_text,
    script_tool_call,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    deliveries_of_line,
    direct_lines,
)
from app.modules.channels.infrastructure.models import ChannelMessageModel
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    invite_org_member,
    signup_user,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

COLLEAGUE_TELEGRAM_ID = 700300
ASKED = "Does the 14th work for you for the offsite?"
ANSWER = "Yes, the 14th works for me."


async def _start_in_app_turns() -> None:
    registry = WorkRegistry()
    register_in_app_turn_work(registry)
    await make_due(async_session_maker)
    with suppress_agent_run_enqueue():
        await run_until_idle(
            async_session_maker, registry=registry, classes=[WorkClass.INTERACTIVE]
        )


async def _settle_answers() -> int:
    """What closing a question sets off: the asked-questions check, then the
    asking conversation's turn."""
    registry = WorkRegistry()
    register_asked_questions_work(registry)
    register_run_followup_work(registry)
    await make_due(async_session_maker)
    with suppress_agent_run_enqueue():
        return await run_until_idle(
            async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
        )


async def _tool_results(client: AsyncClient, *, pod_id: str, conversation_id) -> dict:
    messages = await _messages_for_conversation(
        client, pod_id=pod_id, conversation_id=str(conversation_id)
    )
    return {
        message["tool_call_id"]: json.dumps(message.get("tool_result"))
        for message in messages
        if message.get("tool_call_id") and message.get("tool_result") is not None
    }


async def test_a_question_to_a_colleague_is_answered_on_telegram_and_wakes_the_asker(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    pod_id, owner = test_pod["id"], UUID(fixed_test_user["id"])
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    colleague = await signup_user(async_client, "offsite-colleague")
    member = await invite_org_member(
        authenticated_client, async_client, org_id=fixed_test_org["id"], user=colleague
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=member["id"],
        role="POD_EDITOR",
    )
    colleague_id = UUID(colleague["id"])
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(COLLEAGUE_TELEGRAM_ID),
        resolved_user_id=colleague_id,
    )
    # The colleague has a chat with the pod's bot: somewhere private to reach.
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_payload(
                text="hi", message_id=1, sender_id=COLLEAGUE_TELEGRAM_ID
            ),
            headers={},
        ),
        script=[script_text("Hello!")],
    )

    # 1. The owner asks, in their own channel; the agent asks the colleague.
    base = f"/pods/{pod_id}/channels"
    [direct] = (await authenticated_client.get(base, params={"kind": "DIRECT"})).json()[
        "items"
    ]
    said = await authenticated_client.post(
        f"{base}/{direct['id']}/messages",
        json={"text": "Ask my colleague whether the 14th works for the offsite."},
    )
    assert said.status_code == 201, said.text
    await _start_in_app_turns()
    db_session.expire_all()
    asking = await db_session.scalar(
        select(ChannelMessageModel.conversation_id).where(
            ChannelMessageModel.id == UUID(said.json()["id"])
        )
    )
    assert asking is not None, "no turn answers the owner's line"
    sent_before = len(message_store.get_all("TELEGRAM"))
    await run_scripted_agent_run(
        db_session,
        conversation_id=asking,
        user_id=owner,
        pod_id=UUID(pod_id),
        agent_name="pod_default",
        script=[
            # Messaging sits behind tool search, so it is found before it is called.
            script_tool_call(
                "search_tools", {"queries": ["message a member"]}, tool_call_id="find"
            ),
            script_tool_call(
                "channel_post",
                {"to": colleague["email"], "text": ASKED, "wants_answer": True},
                tool_call_id="ask-colleague",
            ),
            script_text("I've asked them, and will tell you what they say."),
        ],
    )

    results = await _tool_results(
        authenticated_client, pod_id=pod_id, conversation_id=asking
    )
    assert "Sent on TELEGRAM" in results["ask-colleague"], results
    # A question in the colleague's own channel, waiting on them.
    [question] = [
        line
        for line in await direct_lines(db_session, pod_id=pod_id, user_id=colleague_id)
        if line.interaction_kind == "ASK"
    ]
    question_id = question.id
    assert question.body == ASKED
    assert question.awaits_user_id == colleague_id
    assert question.interaction_state == "OPEN"
    assert question.on_behalf_user_id == owner
    assert question.conversation_id == asking
    [delivery] = await deliveries_of_line(question_id)
    assert (delivery.platform, delivery.status.value) == ("TELEGRAM", "SENT")
    # Carried to their Telegram chat with the bot, saying for whom.
    pushed = (await wait_for_messages(message_store, "TELEGRAM"))[sent_before:]
    [to_colleague] = [
        message
        for message in pushed
        if message.get("chat_id") == str(COLLEAGUE_TELEGRAM_ID)
    ]
    assert "On behalf of" in to_colleague["text"]
    assert "14th" in to_colleague["text"]

    # 2. They answer in words, on Telegram; their agent records it.
    seen = record_model_requests(monkeypatch)
    reply = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_payload(
                text=ANSWER, message_id=2, sender_id=COLLEAGUE_TELEGRAM_ID
            ),
            headers={},
        ),
        script=[
            script_tool_call(
                "answer_question",
                {"question_id": str(question_id), "answer": {"text": ANSWER}},
                tool_call_id="record-answer",
            ),
            script_text("Thanks, I've passed that on."),
        ],
    )
    assert isinstance(reply, SurfaceChatContext)
    assert reply.user_id == colleague_id
    # Their agent was told what is waiting on them before it replied.
    assert any(str(question_id) in request["text"] for request in seen)
    assert all("answer_question" in request["tools"] for request in seen)
    db_session.expire_all()
    closed = await db_session.get(ChannelMessageModel, question_id)
    assert closed is not None and closed.interaction_state == "ANSWERED"
    assert closed.answer == {"text": ANSWER}

    # 3. Nothing else was owed, so the asking conversation gets its turn back.
    await _settle_answers()
    seen.clear()
    await run_scripted_agent_run(
        db_session,
        conversation_id=asking,
        user_id=owner,
        pod_id=UUID(pod_id),
        agent_name="pod_default",
        script=[
            script_tool_call(
                "search_tools", {"queries": ["read channel"]}, tool_call_id="find-read"
            ),
            script_tool_call(
                "channel_read", {"asked_by_me": True}, tool_call_id="read-answers"
            ),
            script_text("They can make the 14th."),
        ],
    )
    assert any(REPLIES_ARRIVED[:40] in request["text"] for request in seen)
    woken = await _tool_results(
        authenticated_client, pod_id=pod_id, conversation_id=asking
    )
    assert ANSWER in woken["read-answers"], woken["read-answers"]
    assert "1 answered, 0 still waiting" in woken["read-answers"]
    lines = (await authenticated_client.get(f"{base}/{direct['id']}/messages")).json()[
        "items"
    ]
    assert lines[-1]["text"] == "They can make the 14th."
