"""A line's name made from a caller's ``Idempotency-Key``.

A line is the same line however often it is sent when the client names it
(``client_message_id``, unique per channel). An ``Idempotency-Key`` is the
caller's own, not the channel's, so the name made from it carries who sent it:
two members who happen to pick one key never name one line.
"""

from __future__ import annotations

import hashlib
from uuid import UUID


def keyed_client_id(idempotency_key: str | None, *, user_id: UUID) -> str | None:
    """The line's name for this caller's key; ``None`` without a key."""
    if idempotency_key is None:
        return None
    return hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).hexdigest()
