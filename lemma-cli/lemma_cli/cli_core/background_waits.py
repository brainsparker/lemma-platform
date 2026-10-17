"""Following a one-shot chat through the agent's background waits.

An agent that delegates to a sub-agent, or waits on a process or a timer, ends
its run there: the run completes with the conversation WAITING, and the answer
arrives in a later run the server starts when the wait resolves. `lemma chat -m`
stopped at the first run's ending and printed only the conversation id, so the
reply landed a minute later in a run nobody was watching.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any

from .io import to_plain
from .sse import SseResponse, StreamEvent
from .state import CliState, err_console

#: How long `lemma chat` follows a conversation through background waits.
DEFAULT_WAIT_TIMEOUT_SECONDS = 900.0

#: Polls of a waiting conversation start this far apart and double up to the
#: cap: a sub-agent often answers in seconds, a timer may take minutes.
FIRST_POLL_SECONDS = 0.5
MAX_POLL_SECONDS = 5.0


def waits_in_background(terminal: StreamEvent | None) -> bool:
    """Whether a run ended by leaving its conversation WAITING."""
    if terminal is None or terminal.type.lower() != "completed":
        return False
    data = terminal.data if isinstance(terminal.data, dict) else {}
    return str(data.get("conversation_status") or "").upper() == "WAITING"


def follow_background_waits(
    state: CliState,
    conversations: Any,
    conversation_id: str,
    terminal: StreamEvent | None,
    *,
    render: Callable[[SseResponse], StreamEvent | None],
    open_stream: Callable[[], SseResponse],
    wait_timeout: float,
) -> None:
    """Render each run the conversation resumes into, until one ends it.

    Stops, leaving the caller to print its resume hint, when the conversation
    waits on a person -- a question or an approval nobody here will answer --
    or when ``wait_timeout`` passes with it still waiting.
    """
    deadline = time.monotonic() + wait_timeout
    announced = False
    while waits_in_background(terminal):
        delay = FIRST_POLL_SECONDS
        while True:
            conversation = to_plain(conversations.get(conversation_id)) or {}
            if conversation.get("has_pending_interaction"):
                return
            if str(conversation.get("status") or "").upper() != "WAITING":
                break
            if not announced and state.output != "json":
                err_console.print("[dim]waiting on the agent's background work…[/dim]")
                announced = True
            if time.monotonic() + delay > deadline:
                return
            time.sleep(delay)
            delay = min(delay * 2, MAX_POLL_SECONDS)
        status = str(conversation.get("status") or "").upper()
        if status in {"RUNNING", "STOP_REQUESTED"}:
            # No run id: the server attaches to whichever run is active now.
            terminal = render(open_stream())
        else:
            # The resumed run finished between two polls. Attaching would
            # replay only its ending, so read what it said instead.
            terminal = render(
                _resumed_messages(conversations, conversation_id, terminal, status)
            )


def _resumed_messages(
    conversations: Any,
    conversation_id: str,
    paused: StreamEvent | None,
    status: str,
) -> SseResponse:
    """The answer of runs that ended unwatched, as the frames a stream sends.

    Everything after the paused run's last message is theirs. Played through
    the ordinary renderer, so text and `--output json` read it as they would
    have read it live.
    """
    listed = to_plain(conversations.messages(conversation_id)) or {}
    items = sorted(listed.get("items") or [], key=lambda m: m.get("sequence") or 0)
    paused_run = paused.agent_run_id if paused else None
    after = max(
        (m["sequence"] for m in items if str(m.get("agent_run_id")) == paused_run),
        default=max(
            (m["sequence"] for m in items if m.get("role") == "user"), default=-1
        ),
    )
    frames: list[dict[str, Any]] = []
    run_id = paused_run
    for message in items:
        if message["sequence"] <= after or message.get("role") != "assistant":
            continue
        run_id = str(message.get("agent_run_id") or run_id)
        if message.get("kind") == "TEXT" and message.get("text"):
            frames.append({"type": "token", "data": message["text"], "kind": "text"})
    if status == "FAILED":
        frames.append({"type": "error", "data": "The resumed run failed."})
    elif status == "STOPPED":
        frames.append({"type": "stopped", "data": {"status": status}})
    else:
        frames.append(
            {
                "type": "completed",
                "data": {"status": status, "conversation_status": status},
            }
        )
    return _Replayed([{**frame, "agent_run_id": run_id} for frame in frames])


class _Replayed:
    """Frames already in hand, read the way a live stream is."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = frames

    def iter_lines(self) -> Iterator[str]:
        for frame in self._frames:
            yield f"data: {json.dumps(frame)}"
            yield ""

    def close(self) -> None:
        pass
