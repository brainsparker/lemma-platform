"""Slack and Teams channels, and group DMs, as the pod's GROUP channels.

Every message the bot hears in a routed Slack or Teams channel, or in a group
chat somebody started with it, is a line of a GROUP channel in Lemma; a
platform thread is a thread there, and the bot's answers join it. These read
that timeline back the way a member does, through the channels API.
"""

from __future__ import annotations

import json
import secrets
from typing import Any
from urllib.parse import unquote
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.composition import build_surface_ingress
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    REAL_TEAMS_CHANNEL_ID,
    REAL_TEAMS_TENANT_ID,
    _create_agent,
    _create_agent_surface,
    _ensure_connector_account,
    _load_teams_channel_mention_fixture,
    _seed_external_user,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    slack_delivered,
    wait_for_messages,
    wait_for_slack_text,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads import (
    slack as slack_payloads,
    teams as teams_payloads,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)

pytestmark = pytest.mark.e2e

SUPPORT_CHANNEL = "C0WSCHAN01"
SUPPORT_NAME = "support"
GROUP_CHAT_ID = "19:abc0workspacechannels@thread.v2"


# ── staging ───────────────────────────────────────────────────────────────


def _ts() -> str:
    """A Slack ``ts`` no other test has used: a message is claimed once."""
    return f"17{secrets.randbelow(10**8):08d}.{secrets.randbelow(10**6):06d}"


async def _slack_surface(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod_id: str,
    user_id: str,
    fake_slack: Any,
) -> tuple[dict, dict]:
    """A Slack surface with one routed channel, named."""
    account = await _ensure_connector_account(
        db_session,
        user_id=user_id,
        connector_id="slack",
        credentials={
            "access_token": "xoxb-workspace-channels",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": slack_payloads.BOT_USER_ID,
                "team_id": slack_payloads.TEAM_ID,
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    agent = await _create_agent(client, pod_id)
    response = await client.post(
        f"/pods/{pod_id}/surfaces",
        json={
            "platform": "SLACK",
            "account_id": str(account.id),
            "default_agent_name": agent["name"],
            "config": {
                "channels": [
                    {"channel_id": SUPPORT_CHANNEL, "channel_name": SUPPORT_NAME}
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    return agent, response.json()


def _slack_in_channel(
    text: str, *, ts: str, thread_ts: str | None = None, mention: bool = False
) -> dict[str, Any]:
    return slack_payloads.envelope(
        text=f"<@{slack_payloads.BOT_USER_ID}> {text}" if mention else text,
        ts=ts,
        channel=SUPPORT_CHANNEL,
        channel_type="channel",
        event_type="app_mention" if mention else "message",
        thread_ts=thread_ts,
    )


def _stage_teams(monkeypatch, fake_teams) -> None:
    from app.core.config import settings as app_settings
    from app.modules.agent_surfaces.platforms.teams.adapter import (
        TeamsSurfaceAdapter,
    )

    async def _fake_bot_token(self, tenant_id: str) -> str | None:
        del self, tenant_id
        return "teams-bot-token"

    async def _disable_graph(self, tenant_id: str) -> str | None:
        del self, tenant_id
        return None

    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_bot_token", _fake_bot_token)
    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_graph_token", _disable_graph)
    monkeypatch.setattr(
        surface_settings,
        "microsoft_bot_openid_config_url",
        fake_teams.openid_config_url,
    )
    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "microsoft_bot_app_id", "teams-app-id")


async def _teams_surface(
    client: AsyncClient, db_session: AsyncSession, *, pod_id: str, user_id: str
) -> tuple[dict, dict]:
    """A Teams surface routed to the captured channel only."""
    account = await _ensure_connector_account(
        db_session,
        user_id=user_id,
        connector_id="microsoft_teams",
        credentials={
            "access_token": "teams-token",
            "user_data": {"tenant_id": REAL_TEAMS_TENANT_ID},
        },
    )
    return await _create_agent_surface(
        client,
        pod_id,
        config={
            "type": "TEAMS",
            "account_id": str(account.id),
            "allowed_channel_ids": [REAL_TEAMS_CHANNEL_ID],
        },
    )


def _teams_group_chat(
    *, service_url: str, text: str, activity_id: str, mention: bool
) -> dict[str, Any]:
    """The captured activity, said in a group chat: no team, no channel."""
    payload = teams_payloads.channel_mention(
        service_url=service_url, text=text, activity_id=activity_id
    )
    payload["conversation"] = {
        "isGroup": True,
        "conversationType": "groupChat",
        "tenantId": teams_payloads.TENANT_ID,
        "id": GROUP_CHAT_ID,
    }
    payload["channelData"] = {"tenant": {"id": teams_payloads.TENANT_ID}}
    if not mention:
        payload["text"] = text
        payload.pop("entities", None)
        payload.pop("attachments", None)
    return payload


async def _hear(db_session: AsyncSession, source: str, payload: dict) -> Any:
    """One inbound message through ingress, without driving a run."""
    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(
        SurfacePlatformWebhookIngress(source=source, payload=payload, headers={})
    )
    await uow.commit()
    return context


# ── reading the timeline as a member ──────────────────────────────────────


async def _group_channels(
    client: AsyncClient, pod_id: str, *, platform: str
) -> list[dict]:
    response = await client.get(f"/pods/{pod_id}/channels", params={"kind": "GROUP"})
    assert response.status_code == 200, response.text
    return [
        item
        for item in response.json()["items"]
        if any(source["platform"] == platform for source in item["sources"])
    ]


async def _the_group_channel(
    client: AsyncClient, pod_id: str, *, platform: str
) -> dict:
    channels = await _group_channels(client, pod_id, platform=platform)
    assert len(channels) == 1, channels
    return channels[0]


async def _timeline(client: AsyncClient, pod_id: str, channel_id: str) -> list[dict]:
    response = await client.get(f"/pods/{pod_id}/channels/{channel_id}/messages")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _thread(
    client: AsyncClient, pod_id: str, channel_id: str, message_id: str
) -> list[dict]:
    response = await client.get(
        f"/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/thread"
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _said(lines: list[dict], text: str) -> dict:
    found = [line for line in lines if text in (line["text"] or "")]
    assert len(found) == 1, f"{text!r} in {[line['text'] for line in lines]}"
    return found[0]


# ── Slack ─────────────────────────────────────────────────────────────────


async def test_slack_channel_talk_is_logged_and_its_thread_kept(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    """Unaddressed talk in a routed Slack channel is lines, not answers, and a
    thread reply is a reply under its root."""
    pod_id = test_pod["id"]
    await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    await _seed_external_user(
        db_session,
        platform="SLACK",
        external_user_id=slack_payloads.SENDER_ID,
        tenant_id=slack_payloads.TEAM_ID,
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    root_ts = _ts()
    await _hear(
        db_session, "slack", _slack_in_channel("the deploy is at four", ts=root_ts)
    )
    await _hear(
        db_session,
        "slack",
        _slack_in_channel("moved to five", ts=_ts(), thread_ts=root_ts),
    )

    assert slack_delivered(message_store) == []
    channel = await _the_group_channel(authenticated_client, pod_id, platform="SLACK")
    assert channel["title"] == f"#{SUPPORT_NAME}"

    lines = await _timeline(authenticated_client, pod_id, channel["id"])
    assert [line["text"] for line in lines] == ["the deploy is at four"]
    root = lines[0]
    assert root["sender"]["kind"] == "MEMBER"
    assert root["sender"]["id"] == fixed_test_user["id"]
    assert root["reply_count"] == 1

    replies = await _thread(authenticated_client, pod_id, channel["id"], root["id"])
    assert [line["text"] for line in replies] == ["moved to five"]
    assert replies[0]["thread_root_id"] == root["id"]
    assert replies[0]["sender"]["kind"] == "MEMBER"


async def test_slack_channel_mention_is_answered_in_its_thread(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    """A mention in a routed Slack channel is answered, and the answer is a
    reply under the person's line."""
    pod_id = test_pod["id"]
    agent, _ = await _slack_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    answer = f"Four o'clock, in #support [{uuid4().hex[:6]}]"

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack",
            payload=_slack_in_channel("when is the deploy?", ts=_ts(), mention=True),
            headers={},
        ),
        script=[script_text(answer)],
    )
    assert isinstance(context, SurfaceChatContext)
    await wait_for_slack_text(message_store, answer)

    channel = await _the_group_channel(authenticated_client, pod_id, platform="SLACK")
    lines = await _timeline(authenticated_client, pod_id, channel["id"])
    asked = _said(lines, "when is the deploy?")
    assert asked["sender"]["kind"] == "MEMBER"
    assert asked["reply_count"] == 1
    assert not any(answer in (line["text"] or "") for line in lines)

    replies = await _thread(authenticated_client, pod_id, channel["id"], asked["id"])
    answered = _said(replies, answer)
    assert answered["sender"]["kind"] == "AGENT"
    assert answered["sender"]["id"] == agent["id"]
    assert answered["thread_root_id"] == asked["id"]


# ── Teams ─────────────────────────────────────────────────────────────────


async def test_teams_channel_mention_and_answer_are_the_channel_timeline(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
):
    """The captured Teams channel mention, answered; both are lines of the
    channel's GROUP timeline, the answer in the mention's thread."""
    _stage_teams(monkeypatch, fake_teams)
    pod_id = test_pod["id"]
    await _teams_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
    )

    payload = _load_teams_channel_mention_fixture(fake_teams)
    response = await authenticated_client.post(
        "/surfaces/webhooks/teams",
        content=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": (
                f"Bearer {fake_teams.issue_webhook_token(audience='teams-app-id')}"
            ),
        },
    )
    assert response.status_code == 200, response.text
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="teams", payload=payload, headers={}),
        script=[script_text("E2E agent reply [TEAMS channel line]")],
    )
    assert isinstance(context, SurfaceChatContext)
    await wait_for_messages(
        message_store,
        "TEAMS",
        predicate=lambda item: (
            "E2E agent reply [TEAMS channel line]"
            in str((item.get("body") or {}).get("text") or "")
        ),
    )

    channel = await _the_group_channel(authenticated_client, pod_id, platform="TEAMS")
    lines = await _timeline(authenticated_client, pod_id, channel["id"])
    asked = _said(lines, "Do you know shera?")
    assert asked["sender"]["kind"] == "MEMBER"
    replies = await _thread(authenticated_client, pod_id, channel["id"], asked["id"])
    answered = _said(replies, "E2E agent reply [TEAMS channel line]")
    assert answered["sender"]["kind"] == "AGENT"


async def test_teams_group_chat_needs_no_route_and_answers_only_a_mention(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
):
    """A Teams group chat is a GROUP channel without a route: a mention is
    answered into the chat, and other talk is logged and left alone."""
    _stage_teams(monkeypatch, fake_teams)
    pod_id = test_pod["id"]
    await _teams_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
    )
    answer = "E2E agent reply [TEAMS group chat]"

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="teams",
            payload=_teams_group_chat(
                service_url=fake_teams.service_url,
                text="who has the release notes?",
                activity_id=f"group-chat-{uuid4().hex[:8]}",
                mention=True,
            ),
            headers={},
        ),
        script=[script_text(answer)],
    )
    assert isinstance(context, SurfaceChatContext)
    delivered = await wait_for_messages(
        message_store,
        "TEAMS",
        predicate=lambda item: answer in str((item.get("body") or {}).get("text")),
    )
    to_chat = [
        item
        for item in delivered
        if answer in str((item.get("body") or {}).get("text"))
    ]
    assert GROUP_CHAT_ID in unquote(to_chat[-1]["path"])

    sent_before = len(message_store.get_all("TEAMS"))
    unaddressed = await _hear(
        db_session,
        "teams",
        _teams_group_chat(
            service_url=fake_teams.service_url,
            text="thanks, found them",
            activity_id=f"group-chat-{uuid4().hex[:8]}",
            mention=False,
        ),
    )
    assert not isinstance(unaddressed, SurfaceChatContext)
    assert len(message_store.get_all("TEAMS")) == sent_before

    channel = await _the_group_channel(authenticated_client, pod_id, platform="TEAMS")
    assert channel["title"] == "Group chat"
    lines = await _timeline(authenticated_client, pod_id, channel["id"])
    asked = _said(lines, "who has the release notes?")
    answered = _said(lines, answer)
    unaddressed_line = _said(lines, "thanks, found them")
    assert answered["sender"]["kind"] == "AGENT"
    assert asked["sender"]["kind"] == "MEMBER"
    assert unaddressed_line["sender"]["kind"] == "MEMBER"
    # A group chat has no threads: everything is said in the chat itself.
    assert all(line["thread_root_id"] is None for line in lines)


async def test_teams_personal_chat_is_still_a_dm(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
):
    """A Teams personal chat is answered as a DM and makes no GROUP channel."""
    _stage_teams(monkeypatch, fake_teams)
    pod_id = test_pod["id"]
    await _teams_surface(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
    )

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="teams",
            payload=teams_payloads.personal_dm(
                service_url=fake_teams.service_url,
                text="just between us",
                activity_id=f"personal-{uuid4().hex[:8]}",
                conversation_id=f"a:personal-{uuid4().hex[:8]}",
            ),
            headers={},
        ),
        script=[script_text("E2E agent reply [TEAMS personal]")],
    )
    assert isinstance(context, SurfaceChatContext)
    await wait_for_messages(
        message_store,
        "TEAMS",
        predicate=lambda item: (
            "E2E agent reply [TEAMS personal]"
            in str((item.get("body") or {}).get("text"))
        ),
    )

    assert await _group_channels(authenticated_client, pod_id, platform="TEAMS") == []
