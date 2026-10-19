"""Questions one conversation puts to members (``channel_post`` with
``wants_answer``): how they are named.

A question's ref is ``msg:{asking conversation}:{key}`` (``-`` for none) and
every question one run asks shares a batch -- the run -- so the asking
conversation is woken once, when the last of that batch closes.
"""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID

#: What a message's question ``ref`` starts with.
MESSAGE_REF = "msg"


def message_ref(conversation_id: UUID | None, key: str) -> str:
    return f"{MESSAGE_REF}:{conversation_id or '-'}:{key}"


def asked_by_prefix(conversation_id: UUID) -> str:
    """Every question this conversation asked."""
    return f"{MESSAGE_REF}:{conversation_id}:"


def asking_conversation(ref: str) -> UUID | None:
    """The conversation that asked, from a message question's ref."""
    kind, _, rest = ref.partition(":")
    conversation, _, key = rest.partition(":")
    if kind != MESSAGE_REF or conversation in ("", "-") or not key:
        return None
    try:
        return UUID(conversation)
    except ValueError:
        return None


#: The longest client id a channel line takes.
MAX_CLIENT_ID = 64


def line_client_id(key: str) -> str:
    """A line's client id from a key of any length: the key itself when it
    fits, else a digest of it -- the same key is always the same line."""
    if len(key) <= MAX_CLIENT_ID:
        return key
    return "h:" + sha256(key.encode()).hexdigest()[: MAX_CLIENT_ID - 2]


def batch_of(run_id: UUID | None) -> str | None:
    """The batch a run's questions share."""
    return str(run_id) if run_id is not None else None


__all__ = [
    "MESSAGE_REF",
    "asked_by_prefix",
    "asking_conversation",
    "batch_of",
    "line_client_id",
    "message_ref",
]
