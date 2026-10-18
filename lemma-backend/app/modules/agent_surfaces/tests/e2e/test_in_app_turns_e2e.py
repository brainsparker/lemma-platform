"""Talking to the pod's agents in Lemma's own channels, end to end.

A member writes in their own channel and the pod's agent answers, as a line
there, said for them. In a channel made in Lemma an agent answers only when a
line names it -- and in the thread it was named in. The model's moves are
scripted; everything else is real: the line, the work that starts the turn,
the run, and the work that posts its answer.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.events.in_app_turn_work import (
    register_in_app_turn_work,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    run_scripted_agent_run,
    script_text,
)
from app.modules.channels.infrastructure.models import ChannelMessageModel
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

module_identity = e2e_fixtures.module_identity
fixed_test_user = e2e_fixtures.module_test_user
fixed_test_org = e2e_fixtures.module_test_org


async def _start_turns() -> None:
    registry = WorkRegistry()
    register_in_app_turn_work(registry)
    await make_due(async_session_maker)
    with suppress_agent_run_enqueue():
        await run_until_idle(
            async_session_maker, registry=registry, classes=[WorkClass.INTERACTIVE]
        )


async def _conversation_of(db_session: AsyncSession, message_id: str) -> UUID:
    db_session.expire_all()
    conversation_id = await db_session.scalar(
        select(ChannelMessageModel.conversation_id).where(
            ChannelMessageModel.id == UUID(message_id)
        )
    )
    assert conversation_id is not None, "no turn answers the line"
    return conversation_id


async def _answer(
    db_session: AsyncSession,
    conversation_id: UUID,
    *,
    pod_id: str,
    owner: UUID,
    text: str,
) -> None:
    await run_scripted_agent_run(
        db_session,
        conversation_id=conversation_id,
        user_id=owner,
        pod_id=UUID(pod_id),
        agent_name="pod_default",
        script=[script_text(text)],
    )


async def test_the_pods_agent_answers_a_member_in_their_own_channel(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id, owner = test_pod["id"], UUID(fixed_test_user["id"])
    base = f"/pods/{pod_id}/channels"
    listed = await authenticated_client.get(base, params={"kind": "DIRECT"})
    assert listed.status_code == 200, listed.text
    [direct] = listed.json()["items"]

    said = await authenticated_client.post(
        f"{base}/{direct['id']}/messages", json={"text": "What is on today?"}
    )
    assert said.status_code == 201, said.text
    await _start_turns()
    conversation_id = await _conversation_of(db_session, said.json()["id"])
    await _answer(
        db_session, conversation_id, pod_id=pod_id, owner=owner, text="Two meetings."
    )

    lines = (await authenticated_client.get(f"{base}/{direct['id']}/messages")).json()[
        "items"
    ]
    assert [(line["sender"]["kind"], line["text"]) for line in lines] == [
        ("MEMBER", "What is on today?"),
        ("AGENT", "Two meetings."),
    ]
    answer = lines[1]
    assert answer["on_behalf_of"]["id"] == str(owner)
    assert answer["conversation_id"] == str(conversation_id)
    assert answer["source_id"] is None and not answer["withheld"]

    # The next line is the same conversation's next turn.
    again = await authenticated_client.post(
        f"{base}/{direct['id']}/messages", json={"text": "And tomorrow?"}
    )
    await _start_turns()
    assert await _conversation_of(db_session, again.json()["id"]) == conversation_id


async def test_in_a_channel_an_agent_answers_only_when_named_and_where(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id, owner = test_pod["id"], UUID(fixed_test_user["id"])
    base = f"/pods/{pod_id}/channels"
    made = await authenticated_client.post(base, json={"title": "launch"})
    assert made.status_code == 201, made.text
    channel = made.json()["id"]
    messages = f"{base}/{channel}/messages"

    unnamed = await authenticated_client.post(
        messages, json={"text": "plan for friday"}
    )
    await _start_turns()
    db_session.expire_all()
    assert (
        await db_session.scalar(
            select(ChannelMessageModel.conversation_id).where(
                ChannelMessageModel.id == UUID(unnamed.json()["id"])
            )
        )
    ) is None

    named = await authenticated_client.post(
        messages,
        json={
            "text": "@lem what did we decide?",
            "thread_root_id": unnamed.json()["id"],
            "mentions": [{"kind": "AGENT", "id": pod_id}],
        },
    )
    assert named.status_code == 201, named.text
    await _start_turns()
    conversation_id = await _conversation_of(db_session, named.json()["id"])
    await _answer(
        db_session, conversation_id, pod_id=pod_id, owner=owner, text="Ship on friday."
    )

    thread = (
        await authenticated_client.get(f"{messages}/{unnamed.json()['id']}/thread")
    ).json()["items"]
    assert [(line["sender"]["kind"], line["text"]) for line in thread] == [
        ("MEMBER", "@lem what did we decide?"),
        ("AGENT", "Ship on friday."),
    ]
    timeline = (await authenticated_client.get(messages)).json()["items"]
    assert [line["text"] for line in timeline] == ["plan for friday"]
    assert timeline[0]["reply_count"] == 2
