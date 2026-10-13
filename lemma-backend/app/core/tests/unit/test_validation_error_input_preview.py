"""A refused request is not echoed back whole.

Each validation error carries the value it refused as ``input``. For an
over-long message that was all of it -- sixty thousand characters returned to
say that sixty thousand characters is too many -- and for a missing field, the
whole body. The envelope keeps every key; only a long ``input`` is cut to a
preview that says how much it was.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.api.exception_handlers import (
    VALIDATION_INPUT_PREVIEW_CHARS,
    register_exception_handlers,
)

pytestmark = pytest.mark.unit


class _Message(BaseModel):
    content: str = Field(max_length=100)
    title: str


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/messages")
    async def _send(body: _Message) -> dict[str, str]:
        return {"ok": body.content}

    return TestClient(app, raise_server_exceptions=False)


def _errors(response) -> list[dict]:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    return body["details"]


def test_an_over_long_value_comes_back_as_a_preview(client) -> None:
    content = "x" * 60_000

    (error,) = _errors(
        client.post("/messages", json={"content": content, "title": "t"})
    )

    assert error["loc"] == ["body", "content"]
    assert error["type"] == "string_too_long"
    assert error["input"].startswith("x" * VALIDATION_INPUT_PREVIEW_CHARS)
    assert "60000 characters" in error["input"]
    assert len(error["input"]) < VALIDATION_INPUT_PREVIEW_CHARS + 50
    assert len(client.post("/messages", json={"content": content}).text) < 2_000


def test_a_short_value_is_echoed_unchanged(client) -> None:
    (error,) = _errors(client.post("/messages", json={"content": 7, "title": "t"}))

    assert error["input"] == 7


def test_a_whole_body_echoed_for_a_missing_field_is_cut_too(client) -> None:
    (error,) = _errors(
        client.post("/messages", json={"content": "ok", "notes": "y" * 5_000})
    )

    assert error["loc"] == ["body", "title"]
    assert error["type"] == "missing"
    assert isinstance(error["input"], str)
    assert len(error["input"]) < VALIDATION_INPUT_PREVIEW_CHARS + 50
