"""A delivery claim outlives the platform's own redelivery."""

from __future__ import annotations

import fakeredis
import pytest

from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
    RedisSurfaceEventDedupStore,
)

pytestmark = pytest.mark.asyncio

_DAY = 24 * 3600


async def _claim_ttl(platform: str) -> int:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = RedisSurfaceEventDedupStore(ttl_seconds=900)
    store._redis = redis

    assert await store.claim_message(
        surface_installation_id=None,
        platform=platform,
        external_channel_id="c",
        external_thread_id="t",
        external_message_id="m",
    )
    [key] = [key async for key in redis.scan_iter("agent_surfaces:event_dedup:*")]
    return await redis.ttl(key)


@pytest.mark.parametrize("platform", ["WHATSAPP", "TEAMS"])
async def test_a_platform_that_redelivers_late_holds_its_claim_a_day(platform):
    assert await _claim_ttl(platform) >= _DAY


async def test_other_platforms_keep_the_short_claim():
    assert await _claim_ttl("SLACK") <= 900
