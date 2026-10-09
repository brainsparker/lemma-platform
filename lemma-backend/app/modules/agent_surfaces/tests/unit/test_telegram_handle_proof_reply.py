"""What a Telegram sender whose handle a profile declares is told.

The handle is not proof (see ``test_identity_resolution``), so such a sender is
nobody -- but not a stranger to point at signup: the account may well be
theirs. They are asked once to prove it with their own contact, and told
plainly when the contact they shared proved nothing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.telegram.adapter import (
    TelegramSurfaceAdapter,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    deliver_fallback_reply,
    unresolved_sender_context,
)

pytestmark = pytest.mark.unit

_CLAIMED = ResolvedSurfaceUser(external_user_id="700", unproven_claim=True)


def _event(*, is_dm: bool = True, **metadata: object) -> ParsedInboundSurfaceEvent:
    shared = metadata.get("contact_shared_by_sender") is True
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=(
            ConversationType.EXTERNAL_DM if is_dm else ConversationType.EXTERNAL_GROUP
        ),
        external_thread_id="700",
        external_message_id="1",
        sender_external_user_id="700",
        sender_phone="15550100" if shared else None,
        message_text="" if shared else "hello",
        is_dm=is_dm,
        metadata={"sender_username": "asha", **metadata},
    )


def _surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.TELEGRAM,
        account_id=uuid4(),
        config=SurfaceConfig(),
        is_active=True,
    )


def _context(event: ParsedInboundSurfaceEvent, sender: ResolvedSurfaceUser):
    return unresolved_sender_context(
        surface=_surface(),
        parsed=event,
        adapter=TelegramSurfaceAdapter(),
        agent_display_name="Lem",
        sender=sender,
    )


def test_a_claimed_handle_is_asked_for_proof_not_sent_to_signup() -> None:
    context = _context(_event(), _CLAIMED)

    assert context is not None
    assert context.reply_kind == "identity_proof"
    assert "confirm the account is yours" in context.reply_message
    keyboard = context.reply_metadata["reply_markup"]["keyboard"]
    assert keyboard[0][0]["request_contact"] is True


def test_a_sender_no_profile_claims_is_asked_as_before() -> None:
    context = _context(_event(), ResolvedSurfaceUser(external_user_id="700"))

    assert context is not None
    assert context.reply_kind == "identity_link"
    assert "Lemma account" not in context.reply_message


def test_a_contact_that_proved_nothing_is_said_to_have_proved_nothing() -> None:
    context = _context(_event(contact_shared_by_sender=True), _CLAIMED)

    assert context is not None
    # Answers what they just did, so it is not held to the stranger window.
    assert context.reply_kind == "identity_link"
    assert "isn't verified" in context.reply_message


def test_someone_elses_contact_is_refused_as_it_always_was() -> None:
    context = _context(_event(contact_shared_by_sender=False), _CLAIMED)

    assert context is not None
    assert "your own phone number" in context.reply_message


def test_a_claimed_handle_in_a_group_is_not_asked_in_front_of_the_room() -> None:
    assert _context(_event(is_dm=False), _CLAIMED) is None


async def test_the_proof_prompt_is_sent_once_however_often_they_write() -> None:
    """Asked unprompted, it is a nudge, and a nudge is held to the window."""
    context = _context(_event(), _CLAIMED)
    assert context is not None
    adapter = SimpleNamespace(deliver=AsyncMock(return_value=None))
    store = SimpleNamespace(
        claim_stranger_reply=AsyncMock(side_effect=[True, False, False])
    )

    for _ in range(3):
        await deliver_fallback_reply(
            adapter=adapter,
            context=context,
            credentials={"bot_token": "token"},
            event_dedup_store=store,
        )

    assert adapter.deliver.await_count == 1
