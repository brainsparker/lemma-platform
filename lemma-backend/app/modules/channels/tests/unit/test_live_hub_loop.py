"""The channels hub's loop outlives a fault, and the answer relay keeps what
a reader joining mid-answer is sent.

Nothing here reads Postgres or Redis: the hub's unit of work does nothing, its
read is a stand-in that fails on cue, and the relay's stream is a queue.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.core.live.config import LiveSettings
from app.core.live.protocol import JsonObject
from app.core.live.signals import SignalListeners
from app.core.live.topics import LiveRegistry
from app.modules.channels.services.live.hub import ChannelsHub
from app.modules.channels.services.live.instruments import backoff
from app.modules.channels.services.live.relay import (
    SNAPSHOT_CHARS,
    AnswerRelays,
    Answering,
)

pytestmark = pytest.mark.unit


class _Session:
    async def scalar(self, _statement: object) -> datetime:
        return datetime.now(timezone.utc)


class _UnitOfWork:
    """What a hub with nobody watching reads through: the database's clock."""

    def __init__(self, fault: BaseException | None) -> None:
        self._fault = fault
        self.session = _Session()

    async def __aenter__(self) -> _UnitOfWork:
        if self._fault is not None:
            raise self._fault
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Database:
    """Opens units of work, failing the first ones as told."""

    def __init__(self, *faults: BaseException) -> None:
        self._faults = list(faults)
        self.opened = 0

    def __call__(self) -> _UnitOfWork:
        self.opened += 1
        return _UnitOfWork(self._faults.pop(0) if self._faults else None)


def _hub(database: _Database) -> ChannelsHub:
    return ChannelsHub(
        uuid4(),
        uow_factory=database,  # type: ignore[arg-type]
        listeners=SignalListeners(),
        streams=LiveRegistry(),
        epoch_of=lambda _pod: asyncio.sleep(0, result=None),  # type: ignore[arg-type,return-value]
        settings=LiveSettings(
            live_authz_check_seconds=3600,
            live_idle_poll_seconds=0.001,
            live_wake_coalesce_ms=0,
        ),
        on_idle=lambda _hub: None,
        backoff=lambda _failures: 0.001,
    )


async def test_the_loop_is_started_again_after_an_unforeseen_error() -> None:
    database = _Database(RuntimeError("a bug"), KeyError("another"))
    hub = _hub(database)
    hub._started = True
    hub._start_loop()
    try:
        for _ in range(1000):
            if database.opened >= 5:
                break
            await asyncio.sleep(0.001)
        assert database.opened >= 5
        assert hub._task is not None and not hub._task.done()
        # A clean read since: the next fault backs off from the start again.
        assert hub._loop_failures == 0
    finally:
        hub._started = False
        task = hub._task
        assert task is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_the_backoff_doubles_to_its_ceiling() -> None:
    waits = [backoff(failures) for failures in range(1, 10)]
    assert waits[:3] == [1.0, 2.0, 4.0]
    assert max(waits) == 30.0 and waits == sorted(waits)


def _answering() -> Answering:
    return Answering(
        channel_id=uuid4(),
        message_id=uuid4(),
        agent_id=uuid4(),
        for_user_id=uuid4(),
        agent_run_id=str(uuid4()),
        source="test",
        key="k",
    )


async def test_the_relay_keeps_the_answer_so_far() -> None:
    answering = _answering()
    events: asyncio.Queue[JsonObject] = asyncio.Queue()
    sent: list[str] = []

    @asynccontextmanager
    async def opener(_key: str) -> AsyncIterator[AsyncIterator[JsonObject]]:
        async def stream() -> AsyncIterator[JsonObject]:
            while True:
                yield await events.get()

        yield stream()

    relays = AnswerRelays(deliver=lambda _c, _u, event: sent.append(event), longest=60)
    relays.follow(answering, opener)
    run = answering.agent_run_id
    for kind, data in (
        ("text", "Dropped"),
        ("stream_reset", ""),
        ("text", "Hello"),
        ("thinking", "hmm"),
        ("text", " there"),
    ):
        await events.put(
            {"type": "token", "kind": kind, "data": data, "agent_run_id": run}
        )
    for _ in range(100):
        if len(sent) == 5:
            break
        await asyncio.sleep(0)
    assert len(sent) == 5
    snapshot = relays.snapshot(answering)
    assert snapshot is not None
    shown = json.loads(snapshot)
    assert shown["kind"] == "snapshot" and shown["delta"] == "Hello there"
    assert relays.following(answering.id)

    await events.put(
        {
            "type": "token",
            "kind": "text",
            "data": "x" * (SNAPSHOT_CHARS * 2),
            "agent_run_id": run,
        }
    )
    for _ in range(100):
        if len(sent) == 6:
            break
        await asyncio.sleep(0)
    bounded = json.loads(relays.snapshot(answering) or "{}")
    assert len(bounded["delta"]) == SNAPSHOT_CHARS

    await events.put({"type": "completed", "agent_run_id": run})
    for _ in range(100):
        if not relays.following(answering.id):
            break
        await asyncio.sleep(0)
    assert not relays.following(answering.id)
    assert relays.snapshot(answering) is None
    await relays.close()
