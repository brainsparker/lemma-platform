"""``python -m app.cutover_check`` against real Redis and Postgres.

It reports where the cutover is and gates nothing: finished once the old
tables are gone (the next release's ``app.cutover_finish`` drops them) and no
sweep or converted delivery is left; in progress, with what each route is
still owed, whether the sweep has converted, the streaq leftovers and the
legacy groups' backlog, while the old transport's tables remain.
"""

from __future__ import annotations

import pickle

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.infrastructure.redis.client import get_redis
from app.cutover_check import cutover_report
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.legacy_transport import (
    create_legacy_tables,
    event,
    outbox_row,
)

pytestmark = [pytest.mark.e2e]

postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager

_GROUP = ("surface_events", "analytics-surface")
_JOB = b"streaq:default:task:data:e2e-cutover-check"


async def test_the_check_reports_the_cutover_without_gating(
    e2e_settings, db_manager
) -> None:
    engine = create_async_engine(e2e_settings.database_url)
    redis = get_redis(decode_responses=False)

    async def check():
        return await cutover_report(
            session_maker=db_manager.session_factory, datastore_engine=engine
        )

    async def sql(statement: str) -> None:
        async with db_manager.session_factory() as session:
            await session.execute(text(statement))
            await session.commit()

    await redis.delete(_GROUP[0], _JOB)
    try:
        # No old tables (the e2e schema has none; an install has none once
        # ``app.cutover_finish`` ran): once its sweep has run nothing is left.
        await sql("DELETE FROM work_items WHERE kind LIKE 'cutover.%'")
        report = await check()
        assert report.finished, report
        assert not report.legacy_tables

        # An upgraded install mid-cutover.
        async with engine.begin() as conn:
            await create_legacy_tables(conn)
            await outbox_row(conn, event("pod.join_request.requested"))
        await sql(
            "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
            "VALUES (gen_random_uuid(), 'system', 'admin', 'cutover.legacy_sweep', "
            "'{}', 'cutover.legacy_sweep', now())"
        )
        await redis.xadd(_GROUP[0], {b"__data__": b"{}"})
        await redis.xgroup_create(_GROUP[0], _GROUP[1], id="0")
        await redis.set(
            _JOB, pickle.dumps({"f": "process_surface_message", "a": (), "k": {}})
        )
        report = await check()
        assert not report.finished
        assert report.legacy_tables
        assert report.owed == {"pod.join-request-email": 1}
        assert report.sweep == "queued"
        assert not report.sweep_converted
        assert report.streaq_jobs == {"process_surface_message": 1}
        assert report.legacy_groups["surface_events/analytics-surface"] == 1

        # Converted, and still in progress: the tables stay until the drop.
        await sql(
            "UPDATE work_items SET payload = '{\"converted\": true}' "
            "WHERE kind = 'cutover.legacy_sweep'"
        )
        report = await check()
        assert report.sweep_converted
        assert not report.finished
    finally:
        await redis.delete(_GROUP[0], _JOB)
        await sql("DELETE FROM work_items WHERE kind LIKE 'cutover.%'")
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS domain_event_inbox"))
            await conn.execute(text("DROP TABLE IF EXISTS domain_event_outbox"))
        await engine.dispose()
