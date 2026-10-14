"""The shared-line resolver fails soft, and the ownership check fails closed,
on a database that refuses the connection.

A refused or timed-out connection can surface from the driver as a bare
``OSError`` rather than a SQLAlchemy error, and catching only the latter let it
escape both functions -- turning "verification works from settings" into an
exception on any deployment whose settings are merely incomplete.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import pytest
from pydantic import SecretStr

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.contracts import whatsapp

pytestmark = pytest.mark.unit


@asynccontextmanager
async def _refused() -> AsyncIterator[None]:
    raise ConnectionRefusedError(61, "Connect call failed ('127.0.0.1', 5432)")
    yield


@pytest.fixture
def database_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(whatsapp, "async_session_maker", _refused)


async def test_an_unreachable_database_falls_back_to_settings(
    database_refuses: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Incomplete settings, so the resolver has to try the pool.
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", SecretStr("tok"))
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1555")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", None)

    with caplog.at_level("WARNING"):
        configuration = await whatsapp.global_whatsapp_configuration()

    assert configuration.access_token == "tok"
    assert configuration.phone_number_id == "1555"
    assert (
        "agent_surfaces.whatsapp_contract.shared_number_unreadable.degraded"
        in caplog.text
    )


async def test_an_unreachable_database_owns_no_pooled_number(
    database_refuses: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1555")

    with caplog.at_level("WARNING"):
        owned = await whatsapp.deployment_owns_whatsapp_number("1999")

    assert owned is False
    assert (
        "agent_surfaces.whatsapp_contract.number_ownership_unreadable.degraded"
        in caplog.text
    )
