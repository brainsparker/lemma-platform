"""The conversation metadata only the server may write.

Two keys are written once, by the server, and decide what may happen in a
conversation:

* ``ask`` -- which conversation in another pod its answer is owed to
  (``pod_asks``);
* ``retired_outsiders_thread`` -- one of the old shared strangers' threads,
  kept for reading, where nothing runs (``outsiders``).

A client may write any other key. These it can neither set nor drop.

Whom a conversation answers is not metadata at all: it is the conversation's
``contact_id`` column, which no client request reaches.
"""

from __future__ import annotations

from app.modules.agent.domain.outsiders import RETIRED_KEY
from app.modules.agent.domain.pod_asks import with_ask_kept, without_ask


def client_metadata(metadata: dict[str, object] | None) -> dict[str, object] | None:
    """Metadata a client sent for a new conversation, minus the server's keys."""
    asked = without_ask(metadata)
    if not asked or RETIRED_KEY not in asked:
        return asked
    return {key: value for key, value in asked.items() if key != RETIRED_KEY}


def with_server_keys_kept(
    existing: dict[str, object] | None, incoming: dict[str, object] | None
) -> dict[str, object] | None:
    """``incoming`` metadata replacing ``existing``, with the server's keys kept."""
    kept = with_ask_kept(existing, incoming)
    retired = (existing or {}).get(RETIRED_KEY)
    if kept is not None:
        kept = {key: value for key, value in kept.items() if key != RETIRED_KEY}
    if retired is None:
        return kept
    return {**(kept or {}), RETIRED_KEY: retired}
