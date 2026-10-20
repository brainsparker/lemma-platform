"""What `lemma channels` prints for a timeline, a thread, the inbox and a search.

Driven through the real commands with the SDK's answer standing in for the
server: the rendering is the subject, so nothing of it is stubbed.
"""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

import lemma_cli.cli_core.channel_view as view
import lemma_cli.cli_core.io as io_mod
from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import channel_lines, channels

CHANNEL = "01a157cf-a6d5-7134-8301-e4abcbe7ec76"


def _line(seq: int, text: str | None, **extra) -> dict:
    return {
        "id": f"0000000{seq}-0000-4000-8000-000000000000",
        "channel_id": CHANNEL,
        "seq": seq,
        "created_at": f"2026-10-20T14:0{seq}:00Z",
        "sender": {"kind": "MEMBER", "id": "u-1", "name": "Ada Lovelace"},
        "text": text,
        **extra,
    }


class Answers:
    """The pod's channels, answering each call with what it was given."""

    def __init__(self, **answers) -> None:
        self.answers = answers
        self.sources = None

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.answers[name]


@pytest.fixture
def screen(monkeypatch):
    buffer = StringIO()
    wide = Console(file=buffer, width=240, highlight=False)
    monkeypatch.setattr(view, "console", wide)
    monkeypatch.setattr(io_mod, "console", wide)
    return buffer


def _invoke(runner, patch_run, json_state, module, args, **answers):
    fake = Answers(**answers)
    client = SimpleNamespace(pod=lambda pod_id: SimpleNamespace(channels=fake))
    patch_run(module, client=client, state=json_state)
    return runner.invoke(app, ["channels", *args])


def test_the_timeline_shows_who_said_what_and_when(
    runner, patch_run, json_state, screen
):
    page = {
        "items": [
            _line(1, "Launch is Friday", reply_count=3),
            _line(
                2,
                "Draft ready",
                sender={"kind": "AGENT", "id": "a-1", "name": "Scribe"},
                edited_at="2026-10-20T14:05:00Z",
            ),
            _line(3, None, deleted=True),
            _line(4, "secret", withheld=True),
        ],
        "has_more": True,
        "next_before_seq": 1,
    }

    result = _invoke(
        runner,
        patch_run,
        json_state,
        channel_lines,
        ["messages", "list", CHANNEL],
        messages=page,
    )

    assert result.exit_code == 0, result.output
    out = screen.getvalue()
    assert "Ada Lovelace (member)" in out
    assert "Launch is Friday" in out
    assert "3 replies" in out
    assert "Scribe (agent)" in out and "edited" in out
    assert "(deleted)" in out
    assert "(withheld)" in out and "secret" not in out
    assert "2026-10-20 14:01" in out
    assert "--before-seq 1" in out


def test_a_long_line_is_one_line_cut_short_unless_full(
    runner, patch_run, json_state, screen
):
    words = "word " * 60
    page = {"items": [_line(1, f"first\nsecond {words}")], "has_more": False}

    _invoke(
        runner,
        patch_run,
        json_state,
        channel_lines,
        ["messages", "list", CHANNEL],
        messages=page,
    )

    out = screen.getvalue()
    assert "first second" in out
    assert "…" in out
    assert "--full" in out


def test_json_output_is_the_payload_untouched(runner, patch_run, json_state):
    page = {"items": [_line(1, "hi")], "has_more": False}

    result = _invoke(
        runner,
        patch_run,
        json_state,
        channel_lines,
        ["messages", "list", CHANNEL, "--json"],
        messages=page,
    )

    assert result.exit_code == 0, result.output
    assert '"text": "hi"' in result.output


def test_a_thread_starts_with_its_first_line(runner, patch_run, json_state, screen):
    thread = {
        "root": _line(1, "Question for all", reply_count=1),
        "items": [_line(2, "An answer", thread_root_id="r")],
        "has_more": False,
    }

    _invoke(
        runner,
        patch_run,
        json_state,
        channel_lines,
        ["messages", "thread", CHANNEL, "m-1"],
        thread=thread,
    )

    out = screen.getvalue()
    assert out.index("Question for all") < out.index("An answer")


def test_the_inbox_says_what_waits_and_where(runner, patch_run, json_state, screen):
    channel = {"id": CHANNEL, "kind": "GROUP", "title": "[live] launch"}
    asking = _line(
        5,
        "Approve the post?",
        question={"state": "WAITING", "kind": "APPROVAL"},
    )
    inbox = {
        "totals": {"channels": 2, "mentions": 1, "waiting": 1},
        "waiting": [{"channel": channel, "message": asking}],
        "mentions": [{"channel": channel, "message": _line(6, "@you look")}],
    }

    _invoke(runner, patch_run, json_state, channels, ["inbox"], inbox=inbox)

    out = screen.getvalue()
    assert "1 waiting on you" in out
    assert "[live] launch" in out
    assert "Approve the post?" in out and "question: waiting" in out
    assert "@you look" in out


def test_a_search_shows_the_snippet_and_its_channel(
    runner, patch_run, json_state, screen
):
    found = {
        "items": [
            {
                "channel": {"id": CHANNEL, "kind": "GROUP", "title": "Launch"},
                "message": _line(7, "the whole long line about invoices"),
                "rank": 0.9,
                "snippet": "about invoices",
            }
        ],
        "next_cursor": "c2",
    }

    _invoke(
        runner, patch_run, json_state, channels, ["search", "invoices"], search=found
    )

    out = screen.getvalue()
    assert "about invoices" in out and "whole long line" not in out
    assert "Launch" in out and "Ada Lovelace" in out
    assert "--cursor c2" in out


def test_a_malformed_id_says_what_an_id_is_not_a_python_error(runner, monkeypatch):
    """The real path: the CLI's error boundary and the SDK's id check, with a
    client whose server would fail the test if it were ever asked."""
    from contextlib import contextmanager

    import httpx
    from lemma_sdk import Lemma

    import lemma_cli.cli_core.state as state_mod

    asked: list[httpx.Request] = []
    lemma = Lemma(token="t", base_url="https://api.example.test")
    lemma._transport.generated.set_httpx_client(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: asked.append(request) or httpx.Response(500)
            ),
            base_url="https://api.example.test",
        )
    )

    @contextmanager
    def session(_state):
        yield lemma

    monkeypatch.setattr(state_mod, "client_session", session)

    result = runner.invoke(
        app,
        [
            "channels",
            "messages",
            "list",
            "not-a-channel",
            "--pod",
            "22222222-2222-4222-8222-222222222222",
        ],
    )

    assert result.exit_code == 1
    assert "'not-a-channel' is not an id" in result.stderr
    assert "badly formed" not in result.stderr
    assert asked == []


def test_a_line_s_markup_prints_as_typed(runner, patch_run, json_state, screen):
    page = {
        "items": [
            _line(
                1,
                "[bold red]x[/bold red] [link=https://evil]y[/link]",
                sender={"kind": "MEMBER", "name": "[blink]Mallory[/blink]"},
            )
        ],
        "has_more": False,
    }

    _invoke(
        runner,
        patch_run,
        json_state,
        channel_lines,
        ["messages", "list", CHANNEL],
        messages=page,
    )

    out = screen.getvalue()
    assert "[bold red]x[/bold red]" in out
    assert "[link=https://evil]y[/link]" in out
    assert "[blink]Mallory[/blink]" in out
