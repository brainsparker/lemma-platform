"""A refused request is described, not echoed back.

Pydantic attaches to each validation error the value it refused as ``input``
-- the whole body for a missing field. A request is refused for being wrong,
not for being harmless: a malformed header map carries its credential, an
over-long message all of itself. Each error keeps where (``loc``), which rule
(``type``) and the rule's words (``msg``), and the rule's own parameters in
``ctx``; never the input.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import BaseModel, Field, HttpUrl

from app.core.api.exception_handlers import register_exception_handlers

pytestmark = pytest.mark.unit

_SECRET = "sk-live-not-a-real-credential-0123456789"


class _Message(BaseModel):
    content: str = Field(max_length=100)
    title: str


class _Profile(BaseModel):
    base_url: HttpUrl
    headers: dict[str, str]


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/messages")
    async def _send(body: _Message) -> dict[str, str]:
        return {"ok": body.content}

    @app.post("/profiles")
    async def _create(body: _Profile) -> dict[str, str]:
        return {"ok": str(body.base_url)}

    return TestClient(app, raise_server_exceptions=False)


def _errors(response: Response) -> list[dict[str, object]]:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    return body["details"]


def test_a_secret_in_a_refused_field_is_not_sent_back(client: TestClient) -> None:
    response = client.post(
        "/profiles",
        json={
            "base_url": f"not a url {_SECRET}",
            "headers": f"Authorization: Bearer {_SECRET}",
        },
    )

    errors = _errors(response)
    assert _SECRET not in response.text
    assert {tuple(error["loc"]) for error in errors} == {  # type: ignore[arg-type]
        ("body", "base_url"),
        ("body", "headers"),
    }
    assert all(set(error) <= {"loc", "type", "msg", "ctx"} for error in errors)


def test_a_long_body_is_not_sent_back(client: TestClient) -> None:
    notes = "y" * 60_000

    response = client.post(
        "/messages", json={"content": "ok", "notes": notes, "secret": _SECRET}
    )

    (error,) = _errors(response)
    assert error["loc"] == ["body", "title"]
    assert error["type"] == "missing"
    assert "input" not in error
    assert _SECRET not in response.text
    assert len(response.text) < 1_000


def test_the_rule_is_kept_and_the_value_is_not(client: TestClient) -> None:
    response = client.post("/messages", json={"content": "x" * 60_000, "title": "t"})

    (error,) = _errors(response)
    assert error["loc"] == ["body", "content"]
    assert error["type"] == "string_too_long"
    assert error["ctx"] == {"max_length": 100}
    assert "input" not in error
    assert "xxxx" not in response.text
