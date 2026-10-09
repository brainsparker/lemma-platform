from __future__ import annotations

from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.modules.agent_surfaces.config import surface_settings
import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import delete, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.sender_proof import SenderProof
from app.modules.agent_surfaces.infrastructure.models import AgentSurfaceExternalUser
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _conversation_by_external_thread,
    _create_surface,
    _messages_for_conversation,
    _seed_external_user,
    _set_user_mobile_number,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_telegram_secret_headers,
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)

pytestmark = pytest.mark.e2e


async def test_telegram_built_in_dm_surface_uses_default_agent_and_replies(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):

    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "telegram_webhook_secret", "native-secret")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    pod_id = test_pod["id"]
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM"},
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="111222333",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    payload = _telegram_payload(
        text="Help me set up this pod",
        message_id=44,
        sender_id=111222333,
    )
    raw_body = json.dumps(payload).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/telegram",
        content=raw_body,
        headers=build_telegram_secret_headers("native-secret"),
    )
    assert response.status_code == 200, response.text

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=[script_text("E2E agent reply [TELEGRAM]")],
    )
    assert isinstance(context, SurfaceChatContext)
    # The assistant answers, and it has a name now -- it used to be named by
    # having none.
    assert context.agent_name == DEFAULT_POD_AGENT_NAME
    assert context.surface_id == UUID(surface["id"])

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        external_thread_id="111222333",
    )
    assert conversation is not None
    # Answered by the assistant, which now names itself by its row rather
    # than by the absence of one.
    assert conversation["agent_id"] == pod_id

    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert telegram_messages[-1]["chat_id"] == "111222333"
    # Text is rendered as MarkdownV2, so reserved chars ([ ] _) are escaped.
    assert "E2E agent reply" in telegram_messages[-1]["text"]
    assert "TELEGRAM" in telegram_messages[-1]["text"]


def _telegram_group_payload(
    *,
    text: str,
    message_id: int,
    sender_id: int,
    chat_id: int,
    username: str = "surfaceuser",
    mention: bool = True,
    reply_to_bot: bool = False,
    reply_text: str = "",
) -> dict:
    message: dict = {
        "message_id": message_id,
        "from": {
            "id": sender_id,
            "is_bot": False,
            "first_name": "Surface",
            "username": username,
        },
        "chat": {"id": chat_id, "type": "supergroup", "title": "Lemma Test Group"},
        "date": 1700000000,
        "text": text,
    }
    if mention:
        # An @bot mention produces a "mention" entity, which is what Telegram
        # delivers to a privacy-mode bot in a group.
        message["entities"] = [{"type": "mention", "offset": 0, "length": 9}]
    if reply_to_bot:
        # Replying to the bot's own message continues the thread without a mention.
        message["reply_to_message"] = {
            "message_id": message_id - 1,
            "from": {"id": 12345, "is_bot": True, "username": "lemmabot"},
            "text": reply_text or "",
        }
    return {"update_id": message_id + 100000, "message": message}


async def _proven_group_member(
    db_session: AsyncSession, user: dict, mobile_number: str
) -> None:
    """The member declares the group sender's handle and has proven the account.

    The handle alone is not proof (see the handle tests below); what makes the
    group sender this member is the contact they shared once, from the number
    verified on their profile -- written here as the resolution it leaves.
    """
    await _set_user_mobile_number(
        db_session,
        user_id=user["id"],
        mobile_number=mobile_number,
        telegram_username="surfaceuser",
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="900100",
        resolved_user_id=UUID(user["id"]),
        phone=mobile_number,
        proven_by=SenderProof.PHONE,
    )


def _wire_native_telegram(monkeypatch, fake_telegram) -> None:
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )


async def test_telegram_group_mention_routes_and_replies(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """The bot, @mentioned by a pod member in a group, routes to the surface's
    default agent and replies in the group chat."""
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    # The mentioning member is a pod user, resolved by their telegram username.
    await _proven_group_member(db_session, fixed_test_user, "+15559990001")

    payload = _telegram_group_payload(
        text="@lemmabot summarize the thread",
        message_id=71,
        sender_id=900100,
        chat_id=-1001234567890,
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=[script_text("E2E agent reply")],
    )
    assert isinstance(context, SurfaceChatContext)

    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    # Reply went back to the GROUP chat.
    assert telegram_messages[-1]["chat_id"] == "-1001234567890"
    assert "E2E agent reply" in telegram_messages[-1]["text"]


async def test_telegram_group_mention_from_non_member_is_ignored(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """Group mentions stay pod-member gated and never post onboarding/access
    prompts into the group."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_ingress,
    )
    from app.modules.test_support.e2e_authz import signup_user

    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})

    outsider = await signup_user(async_client, "telegram-group-outsider")
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="900101",
        resolved_user_id=UUID(outsider["id"]),
    )

    payload = _telegram_group_payload(
        text="@lemmabot summarize this",
        message_id=73,
        sender_id=900101,
        chat_id=-1004444444444,
    )
    handler = build_surface_ingress(SqlAlchemyUnitOfWork(db_session))
    context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={})
    )

    assert context is None
    assert message_store.get_all("TELEGRAM") == []


async def test_telegram_group_reply_to_bot_continues_without_mention(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """Replying to the bot's own message in a group continues the conversation
    without re-@mentioning it."""
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _proven_group_member(db_session, fixed_test_user, "+15559990003")

    payload = _telegram_group_payload(
        text="and what about next week?",
        message_id=74,
        sender_id=900100,
        chat_id=-1009876543210,
        mention=False,
        reply_to_bot=True,
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=[script_text("ok")],
    )
    assert isinstance(context, SurfaceChatContext)
    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert telegram_messages[-1]["chat_id"] == "-1009876543210"


async def test_telegram_group_injects_reply_as_channel_context(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """In a group, the message the user replied to is handed to the agent as
    background channel context (Telegram bots can't read full history)."""
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _proven_group_member(db_session, fixed_test_user, "+15559990004")

    payload = _telegram_group_payload(
        text="@lemmabot follow up on that",
        message_id=76,
        sender_id=900100,
        chat_id=-1005555555555,
        reply_to_bot=True,
        reply_text="Earlier the team agreed to ship on Friday.",
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=[script_text("noted")],
    )
    assert isinstance(context, SurfaceChatContext)

    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=str(context.conversation_id),
    )
    user_message = next(m for m in messages if m.get("role") == "user")
    channel_context = (user_message.get("metadata") or {}).get("channel_context")
    assert channel_context, channel_context
    assert any("ship on Friday" in (m.get("text") or "") for m in channel_context), (
        channel_context
    )


async def test_telegram_dm_has_no_channel_context(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A 1:1 DM never fetches channel context (single user, no group)."""
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="222333555",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_payload(text="hi", message_id=78, sender_id=222333555),
            headers={},
        ),
        script=[script_text("noted")],
    )
    assert isinstance(context, SurfaceChatContext)

    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=str(context.conversation_id),
    )
    user_message = next(m for m in messages if m.get("role") == "user")
    assert "channel_context" not in (user_message.get("metadata") or {})


async def test_telegram_group_without_mention_is_ignored(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A plain group message that does NOT mention the bot is ignored (no reply),
    so the bot only speaks when addressed."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_ingress,
    )

    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _proven_group_member(db_session, fixed_test_user, "+15559990002")

    payload = _telegram_group_payload(
        text="just chatting, no bot here",
        message_id=72,
        sender_id=900100,
        chat_id=-1001234567890,
        mention=False,
    )
    handler = build_surface_ingress(SqlAlchemyUnitOfWork(db_session))
    context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={})
    )
    assert context is None
    assert message_store.get_all("TELEGRAM") == []


async def test_telegram_group_mention_of_other_user_is_ignored(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A group message that @mentions ANOTHER user (not the bot) must not wake
    the bot. The `mention` entity is just a plain @username and doesn't identify
    the mentioned user, so the parser records it for bot-identity verification
    and the ingress enrichment confirms it is not the bot before ignoring."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_ingress,
    )

    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _proven_group_member(db_session, fixed_test_user, "+15559990005")

    payload = _telegram_group_payload(
        text="@otherperson can you check this?",
        message_id=80,
        sender_id=900100,
        chat_id=-1007777777777,
        mention=True,
    )
    # Override the default mention entity to point at "@otherperson" (13 chars)
    # so the parser extracts "otherperson" rather than the default "@lemmabot".
    payload["message"]["entities"] = [
        {"type": "mention", "offset": 0, "length": 13},
    ]
    handler = build_surface_ingress(SqlAlchemyUnitOfWork(db_session))
    context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={})
    )
    assert context is None
    assert message_store.get_all("TELEGRAM") == []


async def test_a_telegram_handle_is_proven_by_a_shared_contact_before_it_acts(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A profile's @username says whose account this might be; it proves nothing.

    Anyone can type a handle onto their profile, and a handle given up on
    Telegram is taken by a stranger. So a sender writing as the handle a pod
    member declares reads none of the pod: they are asked, once, to share
    their contact, and the member's verified number is what makes them the
    member -- from then on, on every message.
    """
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="+15559990000",
        telegram_username="surfaceuser",
    )
    sender = 55501234

    async def says(text: str, message_id: int) -> object:
        # _telegram_payload's sender carries username="surfaceuser".
        return await process_ingress_and_run_scripted(
            db_session,
            SurfacePlatformWebhookIngress(
                source="telegram",
                payload=_telegram_payload(
                    text=text, message_id=message_id, sender_id=sender
                ),
                headers={},
            ),
            script=[script_text("E2E agent reply")],
        )

    asked = await says("what is in this pod?", 70)
    await says("hello?", 71)

    assert isinstance(asked, SurfaceReplyContext)
    assert asked.reply_kind == "identity_proof"
    assert (
        await _conversation_by_external_thread(
            authenticated_client, pod_id=pod_id, external_thread_id=str(sender)
        )
        is None
    )
    prompts = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert len(prompts) == 1, "asked once, however often they write"
    assert "confirm the account is yours" in prompts[0]["text"]

    linked = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload={
                "update_id": 100572,
                "message": {
                    "message_id": 72,
                    "from": {
                        "id": sender,
                        "is_bot": False,
                        "first_name": "Surface",
                        "username": "surfaceuser",
                    },
                    "chat": {"id": sender, "type": "private"},
                    "date": 1700000100,
                    "contact": {
                        "phone_number": "+1 555 999 0000",
                        "first_name": "Surface",
                        "user_id": sender,
                    },
                },
            },
            headers={},
        ),
    )
    assert isinstance(linked, SurfaceReplyContext)
    assert "linked now" in linked.reply_message

    chat = await says("now what is in this pod?", 73)
    assert isinstance(chat, SurfaceChatContext)
    assert chat.user_id == UUID(fixed_test_user["id"])
    telegram_messages = await wait_for_messages(
        message_store,
        "TELEGRAM",
        predicate=lambda message: "E2E agent reply" in message.get("text", ""),
    )
    assert "E2E agent reply" in telegram_messages[-1]["text"]
    cached = (
        await db_session.execute(
            select(
                AgentSurfaceExternalUser.resolved_user_id,
                AgentSurfaceExternalUser.proven_by,
            )
            .where(
                AgentSurfaceExternalUser.platform == "TELEGRAM",
                AgentSurfaceExternalUser.external_user_id == str(sender),
            )
            .execution_options(populate_existing=True)
        )
    ).one()
    assert tuple(cached) == (UUID(fixed_test_user["id"]), "PHONE")


async def test_a_binding_a_handle_alone_may_have_made_is_proved_again(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A row written before proof was recorded, by a handle match among others.

    It cannot say what made it, and the member it names declares the handle
    the sender writes with, so it may have been nothing more. The sender is
    asked to prove the account, and the row is unbound -- group authorship and
    member reach read it without asking.
    """
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="+15559990010",
        telegram_username="surfaceuser",
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="55509876",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_payload(
                text="what is in this pod?", message_id=90, sender_id=55509876
            ),
            headers={},
        ),
    )

    assert isinstance(context, SurfaceReplyContext)
    assert context.reply_kind == "identity_proof"
    still_bound = await db_session.scalar(
        select(AgentSurfaceExternalUser.resolved_user_id)
        .where(
            AgentSurfaceExternalUser.platform == "TELEGRAM",
            AgentSurfaceExternalUser.external_user_id == "55509876",
        )
        .execution_options(populate_existing=True)
    )
    assert still_bound is None


async def test_a_profile_giving_up_a_telegram_handle_forgets_who_it_matched(
    db_session: AsyncSession,
    fixed_test_user,
):
    """Only unproven senders who wrote as the old handle lose what it matched.

    A sender who proved the account with their own contact keeps it: renaming
    a profile handle does not undo a proof.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.agent_surfaces.events.handlers import on_identity_event
    from app.modules.identity.domain.events import UserTelegramUsernameChangedEvent
    from app.modules.test_support.fakes import PassthroughEventInbox

    user_id = UUID(fixed_test_user["id"])
    for external_id, handle, proof in (
        ("70001", "@Asha", None),
        ("70002", "someone_else", None),
        ("70003", "asha", SenderProof.PHONE),
    ):
        db_session.add(
            AgentSurfaceExternalUser(
                platform="TELEGRAM",
                tenant_id=None,
                external_user_id=external_id,
                raw_profile={"sender_username": handle},
                resolved_user_id=user_id,
                proven_by=proof,
            )
        )
    await db_session.commit()

    await on_identity_event(
        UserTelegramUsernameChangedEvent(
            user_id=user_id, previous_username="asha"
        ).model_dump(mode="json"),
        uow_factory=SessionUnitOfWorkFactory(
            async_sessionmaker(db_session.bind, expire_on_commit=False)
        ),
        inbox=PassthroughEventInbox(),
    )

    rows = dict(
        (
            await db_session.execute(
                select(
                    AgentSurfaceExternalUser.external_user_id,
                    AgentSurfaceExternalUser.resolved_user_id,
                )
                .where(
                    AgentSurfaceExternalUser.external_user_id.in_(
                        ["70001", "70002", "70003"]
                    )
                )
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    assert rows == {"70001": None, "70002": user_id, "70003": user_id}


async def test_telegram_contact_share_links_user_then_allows_chat(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
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
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="+15551234567",
        telegram_username="surfaceuser",
    )
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})

    contact_payload = {
        "update_id": 100500,
        "message": {
            "message_id": 501,
            "from": {"id": 123123123, "is_bot": False, "first_name": "Linked"},
            "chat": {"id": 123123123, "type": "private"},
            "date": 1700000100,
            "contact": {
                "phone_number": "+1 555 123 4567",
                "first_name": "Linked",
                "user_id": 123123123,
            },
        },
    }

    linked = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=contact_payload,
            headers={},
        ),
        script=[script_text("linked")],
    )
    assert isinstance(linked, SurfaceReplyContext)
    assert "linked now" in linked.reply_message

    chat = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_payload(
                text="Now can you help?",
                message_id=502,
                sender_id=123123123,
            ),
            headers={},
        ),
        script=[script_text("E2E agent reply [TELEGRAM]")],
    )
    assert isinstance(chat, SurfaceChatContext)
    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        external_thread_id="123123123",
    )
    assert conversation is not None
    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=2)
    # Text is rendered as MarkdownV2, so reserved chars ([ ] _) are escaped.
    assert "E2E agent reply" in telegram_messages[-1]["text"]
    assert "TELEGRAM" in telegram_messages[-1]["text"]


async def test_a_deactivated_telegram_handle_resolves_to_nobody(
    db_session: AsyncSession,
    fixed_test_user,
):
    """A departed colleague's @username does not even ask for proof.

    A live member's handle resolves nobody but marks the sender as claiming
    that account, so they are asked to prove it. A deactivated profile has no
    account left to prove: the live-user lookup excludes it, and the sender is
    an ordinary stranger.
    """
    user_id = UUID(fixed_test_user["id"])
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="+15559990099",
        telegram_username="departedcolleague",
    )
    uow = SqlAlchemyUnitOfWork(session=db_session)
    service = SurfaceIdentityResolutionService(
        uow=uow,
        external_user_repository=ExternalSurfaceUserRepository(uow),
    )
    event = ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="900200",
        sender_external_user_id="900200",
        message_text="let me back in",
        is_dm=True,
        metadata={"sender_username": "departedcolleague"},
    )

    live = await service.resolve(event=event)
    assert live.internal_user_id is None
    assert live.unproven_claim is True

    user = await db_session.get(User, user_id)
    assert user is not None
    user.is_active = False
    await db_session.commit()
    await db_session.execute(
        delete(AgentSurfaceExternalUser).where(
            AgentSurfaceExternalUser.external_user_id == "900200"
        )
    )
    await db_session.commit()

    departed = await service.resolve(event=event)
    assert departed.internal_user_id is None
    assert departed.unproven_claim is False
