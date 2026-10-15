"""Dropping the old transport, against real Postgres and Redis: bounded, refused while owed.

* ``finish`` never stalls behind an old process: while another session holds a
  row lock on ``domain_event_outbox`` it gives up within its lock timeout,
  leaves both tables, and says so; once the lock is released it drops them.
* ``python -m app.cutover_finish`` refuses while a delivery is owed, and
  drops both tables once nothing is.
* The cutover sweep finishes on its own with ``WORK_CUTOVER_AUTO_FINISH`` on,
  and with it off converts once and re-arms, keeping the tables for the
  operator.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.infrastructure.cutover.takeover import LEGACY_EVENT, LEGACY_SWEEP
from app.core.infrastructure.cutover.work import (
    SweepState,
    finish,
    register_cutover_work,
)
from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.work import SYSTEM_NAMESPACE, WorkRegistry, WorkWriter
from app.core.infrastructure.work.config import work_settings
from app.core.infrastructure.work.testing import run_until_idle
from app.cutover_finish import EXIT_REFUSED, run
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.legacy_transport import (
    create_legacy_tables,
    event,
    inbox_row,
    outbox_row,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

# Every module's converters, as the worker registers them: the sweep's state
# routes run through them.
import app.modules.agent.events.legacy_cutover  # noqa: E402,F401
import app.modules.agent_surfaces.events.legacy_cutover  # noqa: E402,F401
import app.modules.pod.events.legacy_cutover  # noqa: E402,F401
import app.modules.schedule.events.legacy_cutover  # noqa: E402,F401
import app.modules.workflow.events.legacy_cutover  # noqa: E402,F401

postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager

_OLD = datetime.now(UTC) - timedelta(hours=2)
_PRESENT = (
    "SELECT to_regclass('domain_event_outbox') IS NOT NULL "
    "AND to_regclass('domain_event_inbox') IS NOT NULL"
)
_GONE = (
    "SELECT to_regclass('domain_event_outbox') IS NULL "
    "AND to_regclass('domain_event_inbox') IS NULL"
)


@pytest.fixture
async def legacy(e2e_settings, db_manager):
    """The old tables with one delivery its consumer already settled; removed
    (with the cutover's items) afterwards whatever the test left."""
    engine = create_async_engine(e2e_settings.database_url)
    async with engine.begin() as conn:
        await create_legacy_tables(conn)
        payload = event("pod.join_request.requested")
        row_id = await outbox_row(conn, payload, published=True, occurred_at=_OLD)
        await inbox_row(
            conn,
            "pod.join-request-email",
            row_id,
            "pod.join_request.requested",
            received_at=_OLD,
        )
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM work_items WHERE kind IN (:a, :b)"),
                {"a": LEGACY_SWEEP, "b": LEGACY_EVENT},
            )
            await conn.execute(text("DROP TABLE IF EXISTS domain_event_inbox"))
            await conn.execute(text("DROP TABLE IF EXISTS domain_event_outbox"))
        await engine.dispose()


async def _scalar(db_manager, sql: str, **params: object) -> object:
    async with db_manager.session_factory() as session:
        return await session.scalar(text(sql), params)


async def test_finish_gives_up_on_a_held_lock_and_drops_once_it_is_released(
    legacy, db_manager
) -> None:
    # An old process mid-transaction, holding one outbox row.
    async with legacy.connect() as old:
        await old.execute(text("BEGIN"))
        await old.execute(text("SELECT id FROM domain_event_outbox FOR UPDATE"))
        started = time.monotonic()
        result = await finish(
            db_manager.session_factory,
            since=None,
            lock_timeout_ms=1_000,
            attempts=2,
            backoff_seconds=0.1,
        )
        elapsed = time.monotonic() - started
        assert not result.dropped
        assert result.lock_timeouts == 2
        assert elapsed < 6, f"finish waited {elapsed:.1f}s on a held lock"
        assert await _scalar(db_manager, _PRESENT), "nothing dropped without the lock"
        await old.execute(text("ROLLBACK"))

    result = await finish(db_manager.session_factory, since=None, attempts=1)
    assert result.done
    assert result.lock_timeouts == 0
    assert await _scalar(db_manager, _GONE)
    # Again, with nothing left: a no-op.
    assert (await finish(db_manager.session_factory, since=None)).done


async def test_cutover_finish_refuses_while_a_delivery_is_owed(
    legacy, db_manager
) -> None:
    lines: list[str] = []
    async with legacy.begin() as conn:
        await outbox_row(conn, event("pod.join_request.requested"), published=True)
    code = await run(
        session_maker=db_manager.session_factory,
        datastore_engine=legacy,
        out=lines.append,
    )
    assert code == EXIT_REFUSED, lines
    assert any("pod.join-request-email" in line for line in lines), lines
    assert await _scalar(db_manager, _PRESENT)

    # Taken over (the sweep's work), nothing is owed: it finishes.
    async with legacy.begin() as conn:
        await conn.execute(text("TRUNCATE domain_event_inbox, domain_event_outbox"))
    lines.clear()
    code = await run(
        session_maker=db_manager.session_factory,
        datastore_engine=legacy,
        out=lines.append,
    )
    assert code == 0, lines
    assert await _scalar(db_manager, _GONE)


@pytest.mark.parametrize("auto_finish", [True, False])
async def test_the_sweep_finishes_only_when_auto_finish_is_on(
    legacy, db_manager, monkeypatch, auto_finish: bool
) -> None:
    monkeypatch.setattr(work_settings, "work_cutover_auto_finish", auto_finish)
    redis = get_redis(decode_responses=False)
    await redis.xadd("surface_events", {b"__data__": b"{}"})
    async with db_manager.session_factory() as session:
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
    try:
        await run_until_idle(db_manager.session_factory, registry=registry)
        sweep = await _scalar(
            db_manager,
            "SELECT payload->>'converted' FROM work_items WHERE kind = :k",
            k=LEGACY_SWEEP,
        )
        if auto_finish:
            assert sweep is None, "the sweep finished"
            assert await _scalar(db_manager, _GONE)
            assert not await redis.exists("surface_events"), "legacy streams retired"
        else:
            assert sweep == "true", "converted, and re-armed for the operator"
            assert await _scalar(db_manager, _PRESENT)
            assert await redis.exists("surface_events"), "legacy streams kept"
    finally:
        await redis.delete("surface_events")
