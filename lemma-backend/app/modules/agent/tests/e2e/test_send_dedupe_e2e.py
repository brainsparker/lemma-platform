"""A message sent twice is stored once.

Two ways a send arrives twice. The client retries one it never heard back
from, carrying the same client id; or the same words are sent again moments
later by something else -- a second tab, a double tap. Each copy used to be its
own message, answered twice or starting a second run. Against real Postgres and
Redis, because the first is a lock-and-index question and the second a Redis
window, and a stand-in for either answers whatever it is told.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from starlette import status

from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent.domain.value_objects import MessageRole
from app.modules.agent.services.send_window import WINDOW_SECONDS, SendWindow
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    conversation_service,
    messages,
    owner_uow,
    runs,
    seeded_conversation,
)

pytestmark = [pytest.mark.e2e]


async def _append(scenario, seeded: Seeded, body: dict):
    sent = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations/{seeded.conversation_id}/messages/append",
        json=body,
    )
    assert sent.status_code == status.HTTP_200_OK, sent.text
    return sent.json()


async def _typed(seeded: Seeded, text: str):
    return [
        message
        for message in await messages(seeded.conversation_id)
        if message.role is MessageRole.USER and message.text == text
    ]


async def test_a_retried_send_with_the_same_client_id_is_stored_once(scenario):
    seeded = await seeded_conversation(scenario)
    client_id = f"web-{uuid4()}"

    first = await _append(
        scenario, seeded, {"content": "Draft the memo.", "client_message_id": client_id}
    )
    second = await _append(
        scenario,
        seeded,
        # Reworded on purpose: the id, not the words, says it is the same send.
        {"content": "Draft the memo!", "client_message_id": client_id},
    )

    assert first["started_new_run"] is True and first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert second["started_new_run"] is False
    assert second["agent_run_id"] == first["agent_run_id"]
    assert len(await runs(seeded.conversation_id)) == 1
    (stored,) = await _typed(seeded, "Draft the memo.")
    assert stored.metadata["client_message_id"] == client_id
    assert await _typed(seeded, "Draft the memo!") == []


async def test_a_client_id_sent_only_in_the_metadata_still_counts(scenario):
    """The web app first carried its id in the message's metadata."""
    seeded = await seeded_conversation(scenario)
    body = {"content": "Ping.", "metadata": {"client_message_id": f"web-{uuid4()}"}}

    first = await _append(scenario, seeded, body)
    second = await _append(scenario, seeded, {**body, "content": "Ping again."})

    assert second["deduplicated"] is True
    assert second["agent_run_id"] == first["agent_run_id"]
    assert await _typed(seeded, "Ping again.") == []


async def test_the_same_words_twice_in_a_moment_are_one_message(scenario):
    seeded = await seeded_conversation(scenario)

    first = await _append(scenario, seeded, {"content": "Summarise the inbox."})
    second = await _append(scenario, seeded, {"content": "Summarise the inbox."})
    other = await _append(scenario, seeded, {"content": "And the calendar."})

    assert second["deduplicated"] is True
    assert second["agent_run_id"] == first["agent_run_id"]
    assert len(await _typed(seeded, "Summarise the inbox.")) == 1
    # Different words are a different message, joining the run in flight.
    assert other["deduplicated"] is False
    assert len(await _typed(seeded, "And the calendar.")) == 1
    assert WINDOW_SECONDS == 15


async def test_a_redis_outage_never_stops_a_send(scenario):
    """The window fails open: nobody is kept from sending by a cache."""
    seeded = await seeded_conversation(scenario)
    unreachable = RedisJsonCache(
        redis_url="redis://127.0.0.1:1/0",
        key_prefix="agent:send-window",
        ttl_seconds=15,
    )

    async with owner_uow(seeded) as uow:
        service = conversation_service(uow)
        service.send_window = SendWindow(uow.session, unreachable)
        result = await service.send_from_lemma(
            conversation_id=seeded.conversation_id,
            user_id=seeded.user_id,
            content="Still works?",
            pod_id=seeded.pod_id,
        )

    assert result.started_new_run is True
    assert len(await _typed(seeded, "Still works?")) == 1
