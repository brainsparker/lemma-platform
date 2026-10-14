"""The cutover converter loses nothing the old transport still held, and redoes nothing.

A ``surface_events`` stream is seeded the way prod leaves it at deploy time:
entries the ``surface-webhook-events`` group already finished, one it had
delivered but never acknowledged, and ones it never read -- written as
FastStream writes them (binary envelope and plain). Beside it, an owed reply
in a conversation's metadata and an unpublished outbox row.

``surfaces.drain_events`` must turn exactly the unfinished webhooks into
inbound rows (each once, however often it runs), the owed reply into a
PENDING delivery (clearing the record), the settled notification into the
agent's item, and then exit.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    WorkClass,
    WorkRegistry,
    WorkWriter,
)
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.agent.contracts import conversations_for_surfaces
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.work_payloads import NoPayload
from app.core.infrastructure.cutover.legacy_streams import encode_binary_entry
from app.modules.agent_surfaces.events.drain_work import (
    STREAM,
    register_drain_work,
)
from app.modules.agent_surfaces.events.work_kinds import SURFACE_DRAIN_EVENTS
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _seed_external_user,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_telegram_secret_headers,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    a_telegram_bot,
    drain,
    scalar,
    surface_registry,
)

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("clean_surface_work")]

# The agent's subscriber to ``NotificationSettled`` must be registered for the
# drain's fact to have anywhere to go.
import app.modules.agent.events.run_followup_work  # noqa: E402,F401

GROUP = "surface-webhook-events"


def _webhook(surface_id: str, update: dict) -> dict:
    return {
        "event_type": "surface.webhook.received",
        "event_id": str(uuid4()),
        "source": "telegram",
        "payload": update,
        "headers": {},
        "surface_id": surface_id,
        "source_event_id": f"telegram:{surface_id}:{update['update_id']}",
    }


async def _xadd(redis, event: dict, *, binary: bool) -> str:
    data = json.dumps(event).encode()
    if binary:
        data = encode_binary_entry(data, {"correlation_id": "drain-e2e"})
    entry = await redis.xadd(STREAM, {"__data__": data})
    return entry.decode() if isinstance(entry, bytes) else entry


async def test_the_drain_converts_what_was_left_once_and_exits(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    surface_id, secret, _ = await a_telegram_bot(
        db_session,
        authenticated_client,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        api_base=fake_telegram.api_base,
        bot_token="e2e-drain-token",
        surface_name="drain-bot",
    )
    for sender in (9001, 9002, 9003, 9004):
        await _seed_external_user(
            db_session,
            platform="TELEGRAM",
            external_user_id=str(sender),
            resolved_user_id=UUID(fixed_test_user["id"]),
        )
    # A conversation, through today's path, to owe a reply in.
    response = await authenticated_client.post(
        f"/surfaces/{surface_id}/webhook",
        content=json.dumps(
            _telegram_payload(text="hello", message_id=900, sender_id=9004)
        ).encode(),
        headers=build_telegram_secret_headers(secret),
    )
    assert response.status_code == 200
    await drain(surface_registry())
    conversation_id = await scalar(
        "SELECT conversation_id FROM surface_inbound_messages WHERE surface_id = :s",
        s=surface_id,
    )
    owed_run = uuid4()
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await conversations_for_surfaces.set_conversation_metadata_value(
            uow,
            conversation_id,
            "surface_owed_reply",
            {
                "conversation_id": str(conversation_id),
                "agent_run_id": str(owed_run),
                "message": "The answer the platform refused at 429.",
                "attempt": 1,
                "next_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            },
        )
        await uow.commit()

    redis = get_redis(decode_responses=False)
    await redis.delete(STREAM)
    finished = _telegram_payload(
        text="answered already", message_id=901, sender_id=9001
    )
    pending = _telegram_payload(
        text="in flight at cutover", message_id=902, sender_id=9002
    )
    unread = _telegram_payload(text="never read", message_id=903, sender_id=9003)
    await _xadd(redis, _webhook(surface_id, finished), binary=True)
    await _xadd(
        redis,
        {"event_type": "surface.connected", "surface_id": surface_id},
        binary=False,
    )
    pending_id = await _xadd(redis, _webhook(surface_id, pending), binary=True)
    await redis.xgroup_create(STREAM, GROUP, id="0")
    # The old consumer read everything so far and finished all but the last.
    read = await redis.xreadgroup(GROUP, "old-consumer", {STREAM: ">"}, count=10)
    ids = [entry_id for _, entries in read for entry_id, _ in entries]
    await redis.xack(
        STREAM,
        GROUP,
        *[i for i in ids if (i.decode() if isinstance(i, bytes) else i) != pending_id],
    )
    await _xadd(redis, _webhook(surface_id, unread), binary=False)
    await _xadd(
        redis,
        {
            "event_type": "notification.settled",
            "conversation_id": str(conversation_id),
            "pod_id": test_pod["id"],
            "notification_id": str(uuid4()),
        },
        binary=True,
    )
    await redis.xgroup_create(STREAM, "agent-notification-settled", id=pending_id)

    registry = WorkRegistry()
    register_drain_work(registry, redis=redis, min_drain=timedelta(0))
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await WorkWriter(session, registry=registry).add(
            SURFACE_DRAIN_EVENTS, SYSTEM_NAMESPACE, NoPayload()
        )
        await uow.commit()
    for _ in range(5):
        await make_due(async_session_maker, kind=SURFACE_DRAIN_EVENTS)
        await run_until_idle(
            async_session_maker, registry=registry, classes=[WorkClass.ADMIN]
        )

    assert (
        await scalar(
            "SELECT count(*) FROM work_items WHERE kind = :k", k=SURFACE_DRAIN_EVENTS
        )
        == 0
    ), "the drain exits once nothing is left"
    stored = await scalar(
        "SELECT string_agg(payload->'message'->>'text', '|' ORDER BY received_at) "
        "FROM surface_inbound_messages WHERE surface_id = :s",
        s=surface_id,
    )
    assert stored == "hello|in flight at cutover|never read", stored
    assert (
        await scalar("SELECT count(*) FROM work_items WHERE kind = 'surface.ingest'")
        == 2
    )
    owed = await scalar(
        "SELECT status FROM surface_deliveries WHERE idempotency_key = :k",
        k=f"run-reply:{owed_run}",
    )
    assert owed == "PENDING"
    assert (
        await scalar(
            "SELECT count(*) FROM agent_conversations WHERE id = :c "
            "AND conversation_metadata ? 'surface_owed_reply'",
            c=conversation_id,
        )
        == 0
    ), "the owed record is cleared once it is a delivery"
    assert (
        await scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'agent.replies_arrived'"
        )
        == 1
    )

    # Run it again from scratch: nothing is converted twice.
    async with async_session_maker() as session:
        await session.execute(
            text("DELETE FROM surface_receiver_state WHERE receiver_key LIKE 'drain:%'")
        )
        await session.commit()
        uow = SqlAlchemyUnitOfWork(session)
        await WorkWriter(session, registry=registry).add(
            SURFACE_DRAIN_EVENTS, SYSTEM_NAMESPACE, NoPayload()
        )
        await uow.commit()
    for _ in range(3):
        await make_due(async_session_maker, kind=SURFACE_DRAIN_EVENTS)
        await run_until_idle(
            async_session_maker, registry=registry, classes=[WorkClass.ADMIN]
        )
    assert (
        await scalar(
            "SELECT count(*) FROM surface_inbound_messages WHERE surface_id = :s",
            s=surface_id,
        )
        == 3
    )
    assert (
        await scalar(
            "SELECT count(*) FROM surface_deliveries WHERE agent_run_id = :r",
            r=owed_run,
        )
        == 1
    )
    await redis.delete(STREAM)
