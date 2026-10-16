"""`lemma chat -m` returns once the run it started has ended.

Driven over real HTTP against a loopback stand-in for the API, because the
failure lived in how the CLI reads a live Server-Sent Events connection: it read
until the server closed the stream, and a stream whose terminal frame was lost --
or whose connection was never closed after it -- sat on keepalives for ever. A
one-shot chat whose run had long since completed never returned.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from typer.testing import CliRunner

from lemma_cli.cli_core import sse
from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import conversations as conversations_command

POD_ID = str(uuid.uuid4())
CONVERSATION_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())
AGENT_ID = str(uuid.uuid4())
_NOW = "2026-01-01T00:00:00Z"

#: A chat that names an agent and passes --conversation first checks the
#: conversation belongs to that agent, so the fake answers both lookups.
_LOOKUPS = {
    f"/conversations/{CONVERSATION_ID}": {
        "id": CONVERSATION_ID,
        "pod_id": POD_ID,
        "user_id": str(uuid.uuid4()),
        "agent_id": AGENT_ID,
        "created_at": _NOW,
        "updated_at": _NOW,
        "last_activity_at": _NOW,
        "pod_cwd": "/",
        "workspace_cwd": "/",
    },
    "/agents/coder": {
        "id": AGENT_ID,
        "pod_id": POD_ID,
        "user_id": str(uuid.uuid4()),
        "name": "coder",
        "instruction": "",
        "permissions": {"agent_id": AGENT_ID, "agent_name": "coder"},
        "created_at": _NOW,
        "updated_at": _NOW,
    },
}

#: Far past anything a passing run takes, and short enough that a regression
#: fails the suite rather than hanging it.
GIVE_UP_SECONDS = 20.0


def _frame(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _status() -> bytes:
    return _frame(
        {"type": "status", "agent_run_id": RUN_ID, "data": {"started_new_run": True}}
    )


def _token(text: str) -> bytes:
    return _frame(
        {"type": "token", "agent_run_id": RUN_ID, "data": text, "kind": "text"}
    )


def _completed() -> bytes:
    return _frame(
        {
            "type": "completed",
            "agent_run_id": RUN_ID,
            "data": {"conversation_id": CONVERSATION_ID, "status": "COMPLETED"},
        }
    )


def _failed(reason: str) -> bytes:
    return _frame({"type": "error", "agent_run_id": RUN_ID, "data": reason})


@dataclass
class Scenario:
    """What the stand-in API sends on the send's stream, then on a reattach."""

    sent: list[bytes]
    #: After `sent`: "keepalive" holds the stream open on comment frames, the
    #: way the API does through a quiet run; "silent" holds it open with no
    #: bytes at all, the way a dead connection looks.
    then: str
    reattach: list[bytes] = field(default_factory=list)
    reattached_for: list[str | None] = field(default_factory=list)


class _FakeApi:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.stopping = threading.Event()
        api = self

        class Handler(BaseHTTPRequestHandler):
            def _open_stream(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

            def _hold(self, how: str) -> None:
                while not api.stopping.is_set():
                    if how == "keepalive":
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    time.sleep(0.05)

            def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                if not self.path.endswith(f"/conversations/{CONVERSATION_ID}/messages"):
                    self.send_error(404)
                    return
                self._open_stream()
                try:
                    for chunk in api.scenario.sent:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    self._hold(api.scenario.then)
                except BrokenPipeError, ConnectionResetError:
                    # The CLI hung up, which is what it is meant to do.
                    return

            def do_GET(self) -> None:  # noqa: N802
                url = urlparse(self.path)
                lookup = next(
                    (
                        body
                        for tail, body in _LOOKUPS.items()
                        if url.path.endswith(tail)
                    ),
                    None,
                )
                if lookup is not None:
                    encoded = json.dumps(lookup).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                if not url.path.endswith(f"/conversations/{CONVERSATION_ID}/stream"):
                    self.send_error(404)
                    return
                api.scenario.reattached_for.append(
                    parse_qs(url.query).get("agent_run_id", [None])[0]
                )
                self._open_stream()
                for chunk in api.scenario.reattach:
                    self.wfile.write(chunk)
                    self.wfile.flush()

            def log_message(self, *_args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.stopping.set()
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def fast_rechecks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arrange quiet and dead streams to be noticed in a test's time."""
    monkeypatch.setattr(sse, "QUIET_RECHECK_SECONDS", 0.3)
    monkeypatch.setattr(conversations_command, "STREAM_READ_TIMEOUT_SECONDS", 0.5)


@pytest.fixture
def api(request: pytest.FixtureRequest) -> Iterator[_FakeApi]:
    fake = _FakeApi(request.param)
    yield fake
    fake.close()


def _one_shot(base_url: str) -> tuple[int | None, str]:
    """Run `lemma chat -m` against `base_url`; (None, "") if it never returns."""
    outcome: dict[str, object] = {}

    def invoke() -> None:
        result = CliRunner().invoke(
            app,
            [
                "--base-url",
                base_url,
                "--token",
                "test-token",
                "--pod",
                POD_ID,
                "chat",
                "-a",
                "coder",
                "--conversation",
                CONVERSATION_ID,
                "-m",
                "Reply with exactly: OK",
            ],
        )
        outcome["exit_code"] = result.exit_code
        outcome["output"] = result.output

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    worker.join(GIVE_UP_SECONDS)
    if worker.is_alive():
        return None, ""
    return outcome["exit_code"], str(outcome["output"])  # type: ignore[return-value]


@pytest.mark.parametrize(
    "api",
    [Scenario(sent=[_status(), _token("OK"), _completed()], then="keepalive")],
    indirect=True,
)
def test_a_completed_run_returns_even_if_the_stream_stays_open(
    api: _FakeApi, fast_rechecks: None
) -> None:
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "OK" in output
    assert api.scenario.reattached_for == [], "the ending was heard; nothing to ask"


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("partial"), _failed("Codex did not start in time")],
            then="keepalive",
        )
    ],
    indirect=True,
)
def test_a_failed_run_returns_non_zero_even_if_the_stream_stays_open(
    api: _FakeApi, fast_rechecks: None
) -> None:
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 1, output
    assert "Codex did not start in time" in output


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("OK")],
            then="keepalive",
            reattach=[_completed()],
        )
    ],
    indirect=True,
)
def test_a_run_whose_ending_never_arrived_is_asked_about(
    api: _FakeApi, fast_rechecks: None
) -> None:
    """The terminal frame was lost and the stream carries only keepalives."""
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 0, output
    assert "OK" in output
    assert api.scenario.reattached_for == [RUN_ID]


@pytest.mark.parametrize(
    "api",
    [
        Scenario(
            sent=[_status(), _token("half")],
            then="silent",
            reattach=[_failed("Agent Host run deadline elapsed")],
        )
    ],
    indirect=True,
)
def test_a_dead_connection_to_a_failed_run_is_noticed_and_reported(
    api: _FakeApi, fast_rechecks: None
) -> None:
    """No bytes at all, not even keepalives: the read deadline notices."""
    exit_code, output = _one_shot(api.base_url)

    assert exit_code == 1, output
    assert "Agent Host run deadline elapsed" in output
    assert api.scenario.reattached_for == [RUN_ID]
