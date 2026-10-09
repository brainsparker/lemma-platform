"""A message that will not be answered is never dropped in silence.

The person saw it go. Whether it failed for good in the worker, failed for good
before it reached one, came in empty, or was queued for a private route that
went away, they are either answered the ordinary way or told, once, to send it
again.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.domain.errors import DomainError
from app.core.request_context import bind_job_context
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.models import SurfaceMessageMetadata
from app.modules.agent_surfaces.events import handlers
from app.modules.agent_surfaces.services import lost_delivery, turn_starter
from app.modules.agent_surfaces.services.personal_dm_routes import (
    PersonalRouteUnavailable,
)
from app.modules.agent_surfaces.services.turn_starter import (
    PROCESSING_FAILED_REPLY,
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
    # The claim the first preparation took is given back, or the second is
    # refused as a duplicate.
    starter.event_dedup_store.release_message.assert_awaited_once()
    assert runs == [rerouted]


async def test_an_empty_message_is_answered_with_a_request_to_resend(monkeypatch):
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
    monkeypatch.setattr(turn_starter, "write_inbound_message", AsyncMock())

    await starter.start_agent_chat(_context("   "))

    assert told == [turn_starter._COULD_NOT_READ]


async def test_the_last_failed_try_of_a_queued_message_tells_the_sender(monkeypatch):
    told: list[str] = []
    starter = SimpleNamespace(
        execute_chat=AsyncMock(side_effect=RuntimeError("database gone")),
        tell_sender=AsyncMock(side_effect=lambda _c, message: told.append(message)),
    )
    monkeypatch.setattr(handlers, "build_surface_turn_starter", lambda _f: starter)
    monkeypatch.setattr(
        handlers,
        "streaq_worker",
        SimpleNamespace(context=SimpleNamespace(uow_factory=AsyncMock())),
    )
    payload = handlers.SurfaceProcessMessageTaskPayload(context=_context()).model_dump(
        mode="json"
    )

    for attempt in (1, 2, 3):
        with (
            bind_job_context(
                job_id="j", task_name="process_surface_message", attempt=attempt
            ),
            pytest.raises(RuntimeError),
        ):
            await handlers.process_surface_message.fn(payload)

    # Once, on the last try: the earlier ones are retried.
    assert told == [PROCESSING_FAILED_REPLY]


@pytest.mark.parametrize(
    ("failure", "tries_before_told"),
    [
        (RuntimeError("redis blip"), 3),
        (DomainError("refused", code="X", status_code=400), 1),
    ],
    ids=["retried-then-dead-lettered", "terminal-at-once"],
)
async def test_a_delivery_the_inbox_gives_up_on_tells_the_sender(
    monkeypatch, failure, tries_before_told
):
    counts: dict[str, int] = {}

    class _Redis:
        async def incr(self, key: str) -> int:
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        async def expire(self, key: str, ttl: int) -> None:
            return None

    told = AsyncMock()
    monkeypatch.setattr(lost_delivery, "get_redis", lambda: _Redis())
    monkeypatch.setattr(lost_delivery, "_tell_sender", told)
    received = SurfaceWebhookReceivedEvent(source="slack", payload={})

    for _ in range(tries_before_told):
        await lost_delivery.after_failed_delivery(
            received, failure, uow_factory=AsyncMock()
        )

    told.assert_awaited_once()


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
        conversation_link_repository=AsyncMock(),
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
        conversation_link_repository=AsyncMock(),
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
