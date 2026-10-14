"""``python -m app.cutover_check`` against real Redis and Postgres.

Not ready while an owed legacy group has an entry its converter never
finished, or a streaq job's data is still in Redis; ready once both are gone.
Other groups' backlogs are reported, not gated.
"""

from __future__ import annotations

import pickle

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.infrastructure.redis.client import get_redis
from app.cutover_check import cutover_report
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = [pytest.mark.e2e]

postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager

_OWED = ("surface_events", "surface-webhook-events")
_OTHER = ("surface_events", "analytics-surface")
_JOB = b"streaq:default:task:data:e2e-cutover-check"


async def _clean(redis) -> None:
    await redis.delete(_OWED[0], _JOB)


async def test_the_check_gates_on_owed_groups_and_streaq_leftovers(
    e2e_settings, db_manager
) -> None:
    engine = create_async_engine(e2e_settings.database_url)
    redis = get_redis(decode_responses=False)

    async def check():
        return await cutover_report(
            session_maker=db_manager.session_factory, datastore_engine=engine
        )

    await _clean(redis)
    try:
        # Nothing legacy at all: ready.
        report = await check()
        assert report.ready, report.problems

        # An owed group with an unread entry, and an analytics group with one.
        await redis.xadd(_OWED[0], {b"__data__": b"{}"})
        await redis.xgroup_create(_OWED[0], _OWED[1], id="0")
        await redis.xgroup_create(_OTHER[0], _OTHER[1], id="0")
        await redis.set(
            _JOB, pickle.dumps({"f": "process_surface_message", "a": (), "k": {}})
        )
        report = await check()
        assert not report.ready
        assert report.owed_groups["surface_events/surface-webhook-events"] == 1
        assert report.other_groups["surface_events/analytics-surface"] == 1
        assert report.streaq_jobs == {"process_surface_message": 1}
        assert len(report.problems) == 2, report.problems

        # Converter done: the owed group destroyed, the job gone.
        await redis.xgroup_destroy(_OWED[0], _OWED[1])
        await redis.delete(_JOB)
        report = await check()
        assert report.ready, report.problems
        assert report.owed_groups["surface_events/surface-webhook-events"] is None
    finally:
        await _clean(redis)
        await engine.dispose()
