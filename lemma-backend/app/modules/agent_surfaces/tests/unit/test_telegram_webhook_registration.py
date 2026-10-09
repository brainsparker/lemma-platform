"""Deterministic guards for idempotent Telegram webhook registration (Bug C)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.agent_surfaces.domain.errors import AgentSurfacePlatformError
from app.modules.agent_surfaces.platforms.telegram.client import (
    TelegramApiError,
    TelegramClient,
)
from app.modules.agent_surfaces.services.surface_service import AgentSurfaceService

pytestmark = pytest.mark.asyncio

WEBHOOK_URL = "https://surface-e2e.test/surfaces/abc/webhook"


def _service() -> AgentSurfaceService:
    return AgentSurfaceService(
        surface_repository=AsyncMock(),
        account_binding_resolver=AsyncMock(),
    )


async def test_register_webhook_deletes_then_sets_then_verifies_with_retry(monkeypatch):
    state = {"calls": [], "set_attempts": 0}

    async def fake_call(self, method, payload, client=None):
        state["calls"].append((method, dict(payload)))
        if method == "setWebhook":
            state["set_attempts"] += 1
            if state["set_attempts"] == 1:
                # Transient first failure — registration must retry.
                raise TelegramApiError(method=method, status_code=429, retry_after=0.01)
            return {"ok": True, "result": True}
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": WEBHOOK_URL}}
        return {"ok": True, "result": True}

    monkeypatch.setattr(TelegramClient, "call", fake_call)

    await _service()._register_telegram_webhook(
        credentials={"bot_token": "T", "api_base_url": "http://fake/bot"},
        webhook_url=WEBHOOK_URL,
        webhook_secret="the-secret",
    )

    methods = [m for m, _ in state["calls"]]
    # deleteWebhook (with drop_pending_updates) precedes setWebhook precedes verify.
    assert methods[0] == "deleteWebhook"
    assert state["calls"][0][1]["drop_pending_updates"] is True
    assert methods.index("setWebhook") < methods.index("getWebhookInfo")
    assert state["set_attempts"] == 2  # retried once after the 429

    set_payload = next(p for m, p in state["calls"] if m == "setWebhook")
    assert set_payload["secret_token"] == "the-secret"
    assert set_payload["url"] == WEBHOOK_URL
    assert set_payload["allowed_updates"]
    assert set_payload["drop_pending_updates"] is True


async def test_register_webhook_raises_with_telegram_description(monkeypatch):
    async def fake_call(self, method, payload, client=None):
        if method == "setWebhook":
            raise TelegramApiError(
                method=method,
                status_code=400,
                description="Bad Request: bad webhook: HTTPS url must be provided",
            )
        return {"ok": True, "result": True}

    monkeypatch.setattr(TelegramClient, "call", fake_call)

    with pytest.raises(AgentSurfacePlatformError) as exc_info:
        await _service()._register_telegram_webhook(
            credentials={"bot_token": "T"},
            webhook_url=WEBHOOK_URL,
            webhook_secret="s",
        )
    # The real Telegram description is preserved (no longer a generic error).
    assert "HTTPS url must be provided" in str(exc_info.value)


async def test_an_unconfirmed_webhook_is_reported_not_refused(monkeypatch, caplog):
    """``setWebhook`` was accepted, so Telegram is posting with this secret.

    Raising here rolled the surface back, and Telegram went on posting to a
    surface that no longer existed. The row now stays, and the mismatch is said.
    """

    async def fake_call(self, method, payload, client=None):
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": "https://stale.example/webhook"}}
        return {"ok": True, "result": True}

    monkeypatch.setattr(TelegramClient, "call", fake_call)

    await _service()._register_telegram_webhook(
        credentials={"bot_token": "T"},
        webhook_url=WEBHOOK_URL,
        webhook_secret="s",
    )

    assert "telegram_webhook_mismatch" in caplog.text


def _surface_with_secret():
    from app.modules.agent_surfaces.domain.entities import (
        AgentSurfaceEntity,
        SurfaceConfig,
        SurfacePlatform,
    )
    from uuid import uuid4

    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="telegram",
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(),
        account_id=uuid4(),
    )
    surface.configure_webhook_secret(secret="the-secret")
    return surface


async def test_the_row_is_committed_before_telegram_is_called(monkeypatch):
    from app.modules.agent_surfaces.services import surface_telegram_webhook

    order: list[str] = []

    async def commit(_source) -> None:
        order.append("commit")

    async def fake_call(self, method, payload, client=None):
        order.append(method)
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": WEBHOOK_URL}}
        return {"ok": True, "result": True}

    monkeypatch.setattr(surface_telegram_webhook, "commit_now", commit)
    monkeypatch.setattr(TelegramClient, "call", fake_call)
    service = _service()
    service._build_public_surface_webhook_url = lambda _id: WEBHOOK_URL

    await service._register_committed_telegram_webhook(
        _surface_with_secret(),
        credentials={"bot_token": "T"},
        drop_pending_updates=False,
    )

    assert order[0] == "commit"
    assert order.index("commit") < order.index("setWebhook")


async def test_a_refused_registration_is_recorded_on_the_committed_row(monkeypatch):
    from app.modules.agent_surfaces.domain.entities import AgentSurfaceStatus
    from app.modules.agent_surfaces.services import surface_telegram_webhook

    async def commit(_source) -> None:
        return None

    async def fake_call(self, method, payload, client=None):
        if method == "setWebhook":
            raise TelegramApiError(
                method=method, status_code=400, description="bad webhook"
            )
        return {"ok": True, "result": True}

    monkeypatch.setattr(surface_telegram_webhook, "commit_now", commit)
    monkeypatch.setattr(TelegramClient, "call", fake_call)
    service = _service()
    service._build_public_surface_webhook_url = lambda _id: WEBHOOK_URL
    surface = _surface_with_secret()

    with pytest.raises(AgentSurfacePlatformError):
        await service._register_committed_telegram_webhook(
            surface, credentials={"bot_token": "T"}, drop_pending_updates=False
        )

    saved = service.surface_repository.update.await_args.args[0]
    assert saved.status is AgentSurfaceStatus.ERROR
    assert saved.webhook_secret == "the-secret"


async def test_re_registering_keeps_the_messages_telegram_is_holding(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_call(self, method, payload, client=None):
        calls.append((method, dict(payload)))
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": WEBHOOK_URL}}
        return {"ok": True, "result": True}

    monkeypatch.setattr(TelegramClient, "call", fake_call)

    await _service()._register_telegram_webhook(
        credentials={"bot_token": "T"},
        webhook_url=WEBHOOK_URL,
        webhook_secret="s",
        drop_pending_updates=False,
    )

    assert all(
        payload["drop_pending_updates"] is False
        for method, payload in calls
        if method in {"deleteWebhook", "setWebhook"}
    )
