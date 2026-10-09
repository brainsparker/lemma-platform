"""Approve on a card that a new message already cancelled.

The person is shown an approval, does not tap it, and types something else.
Starting that turn denies the pause on their behalf, so the agent hears "they
moved on" rather than waiting forever. The card is still on their screen. When
they then tap Approve, the decision that stands is the denial -- and the tap
used to be answered "Done", while the action had not run and never would.

Driven through the real interaction path against Postgres: the scripted run
pauses, the second message really supersedes it, and the tap is resolved by
the same code a webhook reaches.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.tests.e2e.helpers import _messages_for_conversation
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    script_request_approval,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_journey import stage_surface

pytestmark = pytest.mark.e2e

TOOL_CALL_ID = "call_superseded_approval_1"


async def test_approve_after_moving_on_says_cancelled_and_runs_nothing(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    platform = SurfacePlatform.TELEGRAM
    stage = await stage_surface(
        platform,
        fake=platform_fake[platform],
        toolsets=["USER_INTERACTION"],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    # One flat script for both turns. The second message reaches the model in
    # the same request as the denial it caused, so the mock counts it as the
    # first run's next turn rather than as a new conversation turn.
    context = await stage.say(
        "please show the widget",
        script=[
            script_request_approval(
                tool_name="display_resource",
                args={"request": {"type": "WIDGET", "content": "<div>Ready</div>"}},
                title="Show a widget",
                reason="Needs your OK first",
                tool_call_id=TOOL_CALL_ID,
            ),
            script_text("It is mid-afternoon in Lisbon."),
        ],
    )
    assert await stage.saw("Show a widget"), "the request never reached the person"
    approve = await stage.control("Approve")

    await stage.say("actually, what time is it in Lisbon?")
    assert await stage.saw("mid-afternoon in Lisbon")

    await stage.press(approve)

    answers = message_store.get_all("TELEGRAM_CALLBACK_ANSWER")
    assert answers, "the tap was answered with nothing"
    said = answers[-1].get("text") or ""
    assert said != "Done"
    assert "already cancelled" in said, said
    assert message_store.get_all("TELEGRAM_EDIT_MARKUP"), (
        "the cancelled card kept its live buttons"
    )

    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=stage.pod_id,
        conversation_id=str(context.conversation_id),
    )
    [result] = [
        message["tool_result"]
        for message in messages
        if message.get("tool_call_id") == TOOL_CALL_ID
        and message.get("kind") == "TOOL_RETURN"
    ]
    assert result["decision"] == "DENY"
    assert result["executed"] is False
