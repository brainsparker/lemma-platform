"""What the server records when authentication fails for a reason nobody expected.

`verify_auth` runs on every authenticated request, and its catch-all arm turned
anything it did not recognise into a bare 401 with one `logger.debug` line and
no traceback. Production runs at `LOG_LEVEL=INFO`, which drops that record
before it is formatted -- so a SuperTokens core outage, a JWKS fetch failure or a
misconfigured key presented as an indistinguishable stream of 401s with nothing
on the server saying why, and every client responded by throwing away a perfectly
good session and asking for a new one.

So the arm reports itself, and answers 503 with a ``Retry-After``: the session
could not be *checked*, which is not the same as it being rejected. With the
database down, the SuperTokens core cannot verify anything -- and a 401 then
signed every person out of the web app and the CLI for the length of a blip.

A token that is simply not valid is not that: it is the normal answer to a
normal request, and saying so per request would bury the case that matters.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from supertokens_python.recipe.session.exceptions import UnauthorisedError

from app.core import security


def _connection():
    return SimpleNamespace(
        scope={"type": "http", "method": "GET"},
        url=SimpleNamespace(path="/api/v1/pods"),
        cookies={},
        headers={},
        state=SimpleNamespace(),
    )


def _errors(caplog) -> list[dict]:
    return [
        record.msg
        for record in caplog.records
        if record.levelno >= logging.WARNING and isinstance(record.msg, dict)
    ]


#: What the deployment failing looks like from ``verify_auth``: the core not
#: answering, and the core answering 500 because its database is down -- the
#: plain ``Exception`` ``supertokens_python``'s querier raises for a core 5xx,
#: which is what the local stack produced with Postgres stopped.
_DEPLOYMENT_FAILURES = [
    RuntimeError("supertokens core unreachable"),
    Exception(
        "SuperTokens core threw an error for a POST request to path: "
        "'/recipe/session/verify' with status code: 500 and message: "
        "StorageQueryException: Connection is not available"
    ),
]


@pytest.mark.parametrize(
    "failure", _DEPLOYMENT_FAILURES, ids=["unreachable", "core-5xx"]
)
async def test_an_unexpected_auth_failure_is_reported_and_answered_retryable(
    caplog, monkeypatch, failure
):
    """Reported with its traceback, and a 503 the client retries -- not a 401
    it answers by discarding the session."""
    monkeypatch.setattr(security, "get_session", AsyncMock(side_effect=failure))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(HTTPException) as raised:
            await security.verify_auth(_connection())

    assert raised.value.status_code == 503
    assert raised.value.headers == {"Retry-After": "2"}
    assert raised.value.detail["code"] == "AUTH_UNAVAILABLE"
    events = _errors(caplog)
    assert [event["event"] for event in events] == [
        "security.auth_dependency.unexpected_failure.degraded"
    ]
    assert str(failure) in events[0]["error_traceback"]


async def test_the_cause_survives_so_the_traceback_points_at_the_fault(monkeypatch):
    cause = RuntimeError("jwks fetch failed")
    monkeypatch.setattr(security, "get_session", AsyncMock(side_effect=cause))

    with pytest.raises(HTTPException) as raised:
        await security.verify_auth(_connection())

    assert raised.value.__cause__ is cause


async def test_an_ordinary_invalid_token_is_not_an_incident(caplog, monkeypatch):
    """The routine answer stays routine; otherwise the log is all 401s."""
    monkeypatch.setattr(
        security,
        "get_session",
        AsyncMock(side_effect=UnauthorisedError("no session")),
    )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(HTTPException) as raised:
            await security.verify_auth(_connection())

    assert raised.value.status_code == 401
    assert _errors(caplog) == []
