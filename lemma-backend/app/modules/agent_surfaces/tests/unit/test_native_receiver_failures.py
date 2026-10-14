"""A native receiver that keeps failing says so, at a level production keeps.

Pollers and socket receivers logged every failure at debug, which
`LOG_LEVEL=INFO` drops before formatting: a revoked bot token looped every five
seconds forever, an email that always failed held the Resend cursor back for
good, and both looked like a bot that had simply gone quiet.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import httpx
import pytest

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.services import resend_polling_receiver
from app.modules.agent_surfaces.services.event_receiver_service import (
    NativeSurfaceReceiverCoordinator,
    _conflict_key,
)
from app.modules.agent_surfaces.services.native_receiver_base import (
    NativeReceiverCandidate,
    NativeReceiverCredentialsRejected,
    ReceiverFailureStreak,
)
from app.modules.agent_surfaces.services.resend_polling_receiver import (
    ResendPollingReceiverRunner,
)
from app.modules.agent_surfaces.services.telegram_polling_runner import (
    TelegramPollingReceiverRunner,
)

pytestmark = pytest.mark.asyncio


def _telegram_runner() -> TelegramPollingReceiverRunner:
    return TelegramPollingReceiverRunner(
        NativeReceiverCandidate(
            key="telegram:system:abc",
            platform=SurfacePlatform.TELEGRAM,
            surface_ids=(),
            credential_label="system",
            credentials={"bot_token": "token-1"},
        )
    )


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://telegram.example.test/getUpdates")
    return httpx.HTTPStatusError(
        "refused", request=request, response=httpx.Response(status, request=request)
    )


@pytest.mark.parametrize("status", [401, 404])
async def test_a_revoked_telegram_token_stands_down_instead_of_looping(status):
    runner = _telegram_runner()
    calls: list[str] = []

    async def fake_telegram_api(client, base_url, method, params):
        calls.append(method)
        if method == "deleteWebhook":
            return {"ok": True}
        raise _http_error(status)

    runner._telegram_api = fake_telegram_api  # type: ignore[method-assign]
    with pytest.raises(NativeReceiverCredentialsRejected):
        await runner.run()

    assert calls == ["deleteWebhook", "getUpdates"]


async def test_a_telegram_poller_that_keeps_failing_warns_once_per_streak(
    monkeypatch, caplog
):
    runner = _telegram_runner()
    polls: list[int] = []

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    async def fake_telegram_api(client, base_url, method, params):
        if method == "deleteWebhook":
            return {"ok": True}
        polls.append(1)
        if len(polls) > 3:
            raise asyncio.CancelledError
        raise _http_error(500)

    runner._telegram_api = fake_telegram_api  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        await runner.run()

    failures = [r for r in caplog.records if "poll_failed" in r.getMessage()]
    assert len(failures) == 1, [r.getMessage() for r in caplog.records]
    assert failures[0].levelno == logging.WARNING


async def test_a_failure_streak_reminds_after_its_interval_and_resets_on_success(
    caplog,
):
    now = [0.0]
    streak = ReceiverFailureStreak(
        platform=SurfacePlatform.RESEND,
        credential_label="system",
        repeat_after_seconds=60,
        clock=lambda: now[0],
    )
    with caplog.at_level(logging.INFO):
        streak.failed(RuntimeError("down"))
        now[0] = 30
        streak.failed(RuntimeError("down"))
        now[0] = 61
        streak.failed(RuntimeError("down"))
        streak.succeeded()
        streak.failed(RuntimeError("down again"))

    messages = [r.getMessage() for r in caplog.records]
    assert sum("poll_failed" in m for m in messages) == 3
    assert sum("poll_recovered" in m for m in messages) == 1
    assert streak.failures == 1


class _CursorRedis:
    """Records the cursors stored, through the shared client `get_redis` reads."""

    def __init__(self) -> None:
        self.stored: list[str] = []

    async def get(self, *_):
        return None

    async def set(self, _key, value, *_args, **_kwargs):
        self.stored.append(value)


def _install_cursor_redis(monkeypatch) -> _CursorRedis:
    from app.core.config import settings
    from app.core.infrastructure.redis import client as redis_client

    redis = _CursorRedis()
    key = (settings.redis_url, True, settings.redis_read_timeout_seconds or None)
    monkeypatch.setitem(redis_client._clients, key, redis)
    return redis


class _FakeResendService:
    def __init__(self, page: list[dict[str, str]]) -> None:
        self._page = page

    async def list_received_emails(self, *, after=None, limit=20):
        return {"data": list(self._page), "has_more": False}


def _resend_runner(ingest) -> ResendPollingReceiverRunner:
    return ResendPollingReceiverRunner(
        NativeReceiverCandidate(
            key="resend:system:abc",
            platform=SurfacePlatform.RESEND,
            surface_ids=(),
            credential_label="system",
            credentials={"api_key": "re_test"},
        ),
        ingest=ingest,
    )


async def test_an_email_that_always_fails_is_skipped_on_the_record_and_the_cursor_moves(
    monkeypatch, caplog
):
    stored = _install_cursor_redis(monkeypatch).stored
    ingested: list[str] = []

    async def ingest(item):
        if item["id"] == "poison":
            raise ValueError("cannot route this one")
        ingested.append(item["id"])

    runner = _resend_runner(ingest)
    # Newest first, as Resend lists them.
    service = _FakeResendService([{"id": "after"}, {"id": "poison"}, {"id": "before"}])

    with caplog.at_level(logging.WARNING):
        cursor = "seed"
        for _ in range(resend_polling_receiver._RESEND_POISON_EMAIL_ATTEMPTS - 1):
            cursor = await runner.poll_once(service, cursor)
            # Held just short of the failing email, which is retried next time.
            assert cursor == "before"
        cursor = await runner.poll_once(service, cursor)

    assert cursor == "after"
    assert stored[-1] == "after"
    assert ingested == ["before", "after"], "the email before it is not re-read"
    skipped = [r for r in caplog.records if "poison_email_skipped" in r.getMessage()]
    assert len(skipped) == 1
    assert skipped[0].levelno == logging.WARNING


async def test_an_email_that_recovers_is_ingested_and_not_skipped(monkeypatch):
    _install_cursor_redis(monkeypatch)
    attempts: list[str] = []

    async def ingest(item):
        attempts.append(item["id"])
        if len(attempts) == 1:
            raise ConnectionError("blip")

    runner = _resend_runner(ingest)
    service = _FakeResendService([{"id": "e1"}])

    assert await runner.poll_once(service, "seed") == "seed"
    assert await runner.poll_once(service, "seed") == "e1"
    assert attempts == ["e1", "e1"]


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def exists(self, key):
        return 1 if key in self.values else 0

    async def get(self, key):
        return self.values.get(key)

    async def expire(self, key, ttl):
        return key in self.values

    async def eval(self, script, numkeys, key, owner):
        if self.values.get(key) == owner:
            self.values.pop(key, None)
        return 1


async def test_a_rejected_credential_stands_the_receiver_down_with_a_warning(
    monkeypatch, caplog
):
    class _RejectedRunner:
        def __init__(self, candidate):
            self._candidate = candidate

        async def run(self):
            raise NativeReceiverCredentialsRejected("token revoked")

    coordinator = NativeSurfaceReceiverCoordinator(
        uow_factory=lambda: None,
        scan_interval_seconds=1,
        redis_url="redis://unused",
        runner_factories={SurfacePlatform.TELEGRAM: _RejectedRunner},
    )
    coordinator._redis = _Redis()
    candidate = _telegram_runner()._candidate
    monkeypatch.setattr(
        coordinator, "_load_candidates", AsyncMock(return_value=[candidate])
    )

    with caplog.at_level(logging.WARNING):
        await coordinator.reconcile()
        task = coordinator._tasks.get(candidate.key)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    assert _conflict_key(candidate.key) in coordinator._redis.values
    assert "native_receiver_credentials_rejected" in caplog.text
