"""A message that will not be answered is never dropped in silence.

The person saw it go. Whether it came in empty or was queued for a private
route that went away, they are either answered the ordinary way or told, once,
to send it again. (A message whose ingest failed for good is the ingest item's
``on_dead``: see ``test_surface_inbound_e2e``.)
"""

from __future__ import annotations

from app.modules.test_support.channel_seats_double import InMemoryChannelSeats

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.models import SurfaceMessageMetadata
from app.modules.agent_surfaces.services import turn_starter
from app.modules.agent_surfaces.services.personal_dm_routes import (
    PersonalRouteUnavailable,
)
from app.modules.agent_surfaces.services.turn_starter import (
    SurfaceTurnStarter,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _registry,
    _slack_event,
)

pytestmark = pytest.mark.asyncio


def _context(text: str = "hello", **overrides: Any) -> SurfaceChatContext:
    event = _slack_event().model_copy(
        update={"message_text": text, "raw_payload": {"event": {"text": text}}}
    )
    return SurfaceChatContext(
        platform="SLACK",
        pod_id=uuid4(),
        surface_id=uuid4(),
        conversation_id=uuid4(),
        user_id=uuid4(),
        message_text=text,
        message_metadata=SurfaceMessageMetadata(surface_platform="SLACK"),
        message_user_id=uuid4(),
        event=event,
        **overrides,
    )


class _UowFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *_: object) -> None:
        return None


def _starter(**kwargs: Any) -> SurfaceTurnStarter:
    return SurfaceTurnStarter(
        uow_factory=_UowFactory(),
        adapter_registry=_registry(AsyncMock()),
        event_dedup_store=SimpleNamespace(
            release_message=AsyncMock(),
            claim_stranger_reply=AsyncMock(return_value=True),
            release_stranger_reply=AsyncMock(),
        ),
        **kwargs,
    )


async def test_a_message_whose_private_route_went_away_goes_the_ordinary_way():
    """Retried three times and dropped, before: the route cannot come back."""
    rerouted = _context("hello, rerouted")
    reprepare = AsyncMock(return_value=rerouted)
    starter = _starter(reprepare=reprepare)
    original = _context(personal_dm_route_id=uuid4())
    runs: list[SurfaceChatContext] = []

    async def start(context: SurfaceChatContext) -> None:
        if context.personal_dm_route_id is not None:
            raise PersonalRouteUnavailable("the route is gone")
        runs.append(context)

    starter.start_agent_chat = start  # type: ignore[method-assign]

    await starter.execute_chat(original)

    [request] = reprepare.await_args.args
    assert isinstance(request, SurfacePlatformWebhookIngress)
    assert request.payload == original.event.raw_payload
    # Nothing is handed back first: the message claim is owned by the inbound
    # row being ingested, so the second preparation is granted it.
    starter.event_dedup_store.release_message.assert_not_awaited()
    assert runs == [rerouted]


async def test_an_empty_message_is_answered_with_a_request_to_resend():
    """The write declines an empty body; the person is told, not ignored."""
    starter = _starter()
    told: list[str] = []

    async def tell(context, message: str) -> None:
        told.append(message)

    starter.tell_sender = tell  # type: ignore[method-assign]
    starter._credentials_for = AsyncMock(return_value={})  # type: ignore[method-assign]
    starter._ingest_files = AsyncMock(  # type: ignore[method-assign]
        return_value=turn_starter.AttachmentIngest()
    )
    starter._name_new_thread = AsyncMock()  # type: ignore[method-assign]

    await starter.start_agent_chat(_context("   "))

    assert told == [turn_starter._COULD_NOT_READ]


async def test_a_sender_profile_the_platform_would_not_give_is_not_cached_as_nobody(
    caplog,
):
    """A Slack or Graph blip on someone's first message made them a stranger.

    The miss is for this message only: it is logged where an operator sees it,
    and resolution is told not to remember it.
    """
    import httpx

    from app.modules.agent_surfaces.services.surface_router import SurfaceRouter

    identity = SimpleNamespace(
        resolve=AsyncMock(return_value=SimpleNamespace(internal_user_id=None))
    )
    router = SurfaceRouter(
        uow=SimpleNamespace(session=SimpleNamespace(info={})),  # type: ignore[arg-type]
        surface_repository=AsyncMock(),
        seats=InMemoryChannelSeats(),
        pod_membership_port=AsyncMock(),
        identity_service=identity,  # type: ignore[arg-type]
        credential_resolver=AsyncMock(),
    )
    adapter = SimpleNamespace(
        fetch_sender_profile=AsyncMock(side_effect=httpx.ConnectError("graph down"))
    )

    await router.resolve_sender(
        adapter=adapter,  # type: ignore[arg-type]
        parsed=_slack_event(),
        credentials={},
    )

    assert identity.resolve.await_args.kwargs["profile_unavailable"] is True
    assert "sender_profile_unavailable" in caplog.text


async def test_a_bug_reading_the_profile_is_not_hidden_as_a_stranger():
    from app.modules.agent_surfaces.services.surface_router import SurfaceRouter

    router = SurfaceRouter(
        uow=SimpleNamespace(session=SimpleNamespace(info={})),  # type: ignore[arg-type]
        surface_repository=AsyncMock(),
        seats=InMemoryChannelSeats(),
        pod_membership_port=AsyncMock(),
        identity_service=AsyncMock(),
        credential_resolver=AsyncMock(),
    )
    adapter = SimpleNamespace(fetch_sender_profile=AsyncMock(side_effect=KeyError("x")))

    with pytest.raises(KeyError):
        await router.resolve_sender(
            adapter=adapter,  # type: ignore[arg-type]
            parsed=_slack_event(),
            credentials={},
        )


# ------------------------------------------- a turn refused before its run


def _not_shareable():
    from app.modules.agent.contracts import RuntimeUnavailableError

    return RuntimeUnavailableError(
        "This teammate runs on Ada's own Claude Code, which only they can use.",
        code="runtime_profile_not_shareable",
    )


def _refusing_starter(monkeypatch, told: list[str], noted: list[dict], heard):
    from app.modules.agent_surfaces.services import refused_turns

    async def refuse(*_args, **_kwargs):
        raise _not_shareable()

    async def tell_member(_uow, **kwargs):
        noted.append(kwargs)

    async def background(*_args, **_kwargs):
        return SimpleNamespace(to_metadata=dict)

    monkeypatch.setattr(turn_starter, "write_inbound_message", refuse)
    monkeypatch.setattr(turn_starter, "run_background", background)
    monkeypatch.setattr(
        turn_starter, "reply_all_audience", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(refused_turns, "tell_member", tell_member)
    starter = _starter(
        channel_lines=lambda _uow: SimpleNamespace(heard=heard),
    )

    async def tell(_context, message: str) -> None:
        told.append(message)

    starter.tell_sender = tell  # type: ignore[method-assign]
    starter._credentials_for = AsyncMock(return_value={})  # type: ignore[method-assign]
    starter._ingest_files = AsyncMock(  # type: ignore[method-assign]
        return_value=turn_starter.AttachmentIngest()
    )
    starter._name_new_thread = AsyncMock()  # type: ignore[method-assign]
    return starter


async def test_a_turn_refused_in_a_group_tells_the_member_once_and_not_the_group(
    monkeypatch,
):
    """Before, the refusal was retried until the message went dead, and a
    group was told nothing: the agent never answered and nobody knew why."""
    told: list[str] = []
    noted: list[dict] = []
    heard = AsyncMock()
    starter = _refusing_starter(monkeypatch, told, noted, heard)
    in_group = _context("@helper what's on today?", agent_display_name="Helper")
    in_group = in_group.model_copy(
        update={"event": in_group.event.model_copy(update={"is_dm": False})}
    )

    await starter.start_agent_chat(in_group)
    await starter.start_agent_chat(in_group)

    assert told == [], "a shared place is not told one person's refusal"
    assert heard.await_count == 2, "what they said still lands on the timeline"
    first, again = noted
    assert first["user_id"] == in_group.user_id
    assert "Ada's own Claude Code" in first["text"]
    assert first["text"].startswith("Helper didn't answer your message on Slack.")
    # The same key both times: the note is said once, not once per message.
    assert first["key"] == again["key"]


async def test_a_turn_refused_in_a_direct_message_says_why_there_too(monkeypatch):
    told: list[str] = []
    noted: list[dict] = []
    starter = _refusing_starter(monkeypatch, told, noted, AsyncMock())

    await starter.start_agent_chat(_context("hello"))

    [said] = told
    assert "Ada's own Claude Code" in said
    assert len(noted) == 1
