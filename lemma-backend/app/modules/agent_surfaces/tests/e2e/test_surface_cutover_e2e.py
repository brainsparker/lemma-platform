"""The cutover loses nothing the old transport owed the surfaces, and redoes nothing.

The outbox is left the way prod leaves it at deploy time: a webhook the
``agent-surfaces.webhook`` consumer had already finished, one it was handed
but never finished, one never published -- each already answered 200 to the
platform -- and a settled notification. Beside it, an owed reply in a
conversation's metadata (the old owed-reply job's record).

The cutover sweep must turn exactly the unfinished webhooks into inbound rows
(each once), the settled notification into the agent's item, and -- once the
previous release is gone -- the owed reply into a PENDING delivery (clearing
the record). With ``WORK_CUTOVER_AUTO_FINISH`` off it keeps the old tables for
the operator's ``app.cutover_finish`` and re-arms itself. Running it again converts nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.core.infrastructure.cutover.takeover import LEGACY_SWEEP
from app.core.infrastructure.cutover.work import SweepState, register_cutover_work
from app.core.infrastructure.db.session import async_session_maker, get_engine
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import SYSTEM_NAMESPACE, WorkRegistry, WorkWriter
from app.core.infrastructure.work.config import work_settings
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.agent.contracts import conversations_for_surfaces
from app.modules.agent_surfaces.config import surface_settings
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
from app.modules.test_support.e2e.legacy_transport import (
    create_legacy_tables,
    inbox_row,
    outbox_row,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.drives_work,
    pytest.mark.usefixtures("clean_surface_work"),
]


@pytest.fixture(autouse=True)
def _worker_registrations() -> None:
    """Every module's converters and fact subscribers, as a worker has them."""
    from app.core.infrastructure.work.process import register_worker_modules
    from app.core.registry.installed import OSS_MODULES

    register_worker_modules(OSS_MODULES)


CONSUMER = "agent-surfaces.webhook"


def _webhook(surface_id: str, update: dict) -> dict:
    return {
        "event_type": "surface.webhook.received",
        "event_id": str(uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "source": "telegram",
        "payload": update,
        "headers": {},
        "surface_id": surface_id,
        "source_event_id": f"telegram:{surface_id}:{update['update_id']}",
    }


async def _sweep() -> None:
    """Queue the sweep, past its quiet window, and run the cutover to idle."""
    async with async_session_maker() as session:
        await session.execute(
            text("DELETE FROM work_items WHERE kind = :k"), {"k": LEGACY_SWEEP}
        )
        await WorkWriter(session).add(
            LEGACY_SWEEP,
            SYSTEM_NAMESPACE,
            SweepState(quiet_seconds=0),
            dedup_key=LEGACY_SWEEP,
        )
        await session.commit()
    registry = WorkRegistry()
    register_cutover_work(registry)
    await run_until_idle(async_session_maker, registry=registry, slots=4)


async def test_the_cutover_converts_what_was_owed_once(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    monkeypatch,
):
    # An operator finishes (cloud): the sweep converts and keeps the tables.
    monkeypatch.setattr(work_settings, "work_cutover_auto_finish", False)
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    surface_id, secret, _ = await a_telegram_bot(
        db_session,
        authenticated_client,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        api_base=fake_telegram.api_base,
        bot_token="e2e-cutover-token",
        surface_name="cutover-bot",
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

    finished = _webhook(
        surface_id,
        _telegram_payload(text="answered already", message_id=901, sender_id=9001),
    )
    pending = _webhook(
        surface_id,
        _telegram_payload(text="in flight at cutover", message_id=902, sender_id=9002),
    )
    unread = _webhook(
        surface_id, _telegram_payload(text="never read", message_id=903, sender_id=9003)
    )
    # Never read either, but from hours before the release: answering it now
    # would reply to a message its sender has long given up on.
    ancient = _webhook(
        surface_id, _telegram_payload(text="hours ago", message_id=904, sender_id=9004)
    )
    async with get_engine().begin() as conn:
        await create_legacy_tables(conn)
        done = await outbox_row(conn, finished, published=True)
        await inbox_row(conn, CONSUMER, done, "surface.webhook.received")
        handed = await outbox_row(conn, pending, published=True)
        await inbox_row(
            conn,
            CONSUMER,
            handed,
            "surface.webhook.received",
            status="RETRYING",
            received_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        await outbox_row(conn, unread)
        await outbox_row(
            conn, ancient, occurred_at=datetime.now(UTC) - timedelta(hours=2)
        )
        await outbox_row(
            conn,
            {
                "event_type": "notification.settled",
                "event_id": str(uuid4()),
                "occurred_at": datetime.now(UTC).isoformat(),
                "conversation_id": str(conversation_id),
                "pod_id": test_pod["id"],
                "notification_id": str(uuid4()),
            },
            published=True,
        )

    await _sweep()

    assert await scalar(
        "SELECT to_regclass('domain_event_outbox') IS NOT NULL "
        "AND to_regclass('domain_event_inbox') IS NOT NULL"
    ), "auto-finish off: the tables stay for the operator"
    assert await scalar(
        "SELECT bool_and((payload->>'converted')::boolean "
        "AND run_at > now() + interval '200 seconds') "
        "FROM work_items WHERE kind = :k AND state = 'queued'",
        k=LEGACY_SWEEP,
    ), "converted once, and re-armed on the slow cadence while the tables exist"
    stored = await scalar(
        "SELECT string_agg(payload->'message'->>'text', '|' "
        "ORDER BY payload->'message'->>'text') "
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

    # Again, from scratch: nothing is converted twice.
    await _sweep()
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
    async with get_engine().begin() as conn:
        await conn.execute(
            text("DELETE FROM work_items WHERE kind = :k"), {"k": LEGACY_SWEEP}
        )
        await conn.execute(text("DROP TABLE IF EXISTS domain_event_inbox"))
        await conn.execute(text("DROP TABLE IF EXISTS domain_event_outbox"))
