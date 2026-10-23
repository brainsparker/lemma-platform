"""The once-a-day ledger behind "Ask members to connect".

It is the whole of the button's promise -- nobody is asked about one connector
twice in a day, whoever presses it -- so it has to keep that promise while Redis
is away too: it asks nobody, rather than everybody again.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.connectors.infrastructure.adapters.member_coverage import (
    ASK_AGAIN_AFTER_SECONDS,
    RedisAskLedger,
)

pytestmark = pytest.mark.unit


class _Cache:
    def __init__(self) -> None:
        self.held: set[str] = set()

    async def set_raw_if_absent(self, suffix: str, payload: str, **_kw) -> bool:
        if suffix in self.held:
            return False
        self.held.add(suffix)
        return True


class _GoneCache:
    async def set_raw_if_absent(self, *_a, **_kw) -> bool:
        raise ConnectionError("redis is away")


async def test_a_member_is_claimed_once_per_connector() -> None:
    ledger = RedisAskLedger(cache=_Cache())
    connector, other, member = uuid4(), uuid4(), uuid4()

    assert await ledger.claim(pod_connector_id=connector, user_id=member)
    assert not await ledger.claim(pod_connector_id=connector, user_id=member)
    assert await ledger.claim(pod_connector_id=other, user_id=member)
    assert await ledger.claim(pod_connector_id=connector, user_id=uuid4())


async def test_an_unreachable_ledger_asks_nobody() -> None:
    ledger = RedisAskLedger(cache=_GoneCache())

    # None, not False: "could not tell", so nobody is asked and the editor is
    # not told they were asked already.
    assert await ledger.claim(pod_connector_id=uuid4(), user_id=uuid4()) is None


def test_the_ledger_forgets_after_a_day() -> None:
    assert ASK_AGAIN_AFTER_SECONDS == 24 * 60 * 60
