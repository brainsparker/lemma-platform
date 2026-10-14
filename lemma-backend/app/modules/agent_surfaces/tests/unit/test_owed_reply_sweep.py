"""The edges of sweeping owed replies back onto the queue.

The journey -- a lost job's reply swept back and delivered once -- is
`tests/e2e/test_owed_reply_recovery_e2e.py`. These pin what it does not reach:
a reply too old to still be wanted, and a record that is not one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.modules.agent.contracts import (
    conversation_metadata_records as metadata_records,
)
from app.modules.agent.contracts.conversation_metadata_records import MetadataRecord
from app.modules.agent_surfaces.services.owed_reply import OwedReply
from app.modules.agent_surfaces.services.owed_reply_sweep import (
    GIVE_UP_AFTER,
    sweep_owed_replies,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


class _UowFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *_: object) -> None:
        return None


class _Queue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    async def enqueue(self, job_name: str, **kwargs: Any) -> None:
        self.jobs.append({"name": job_name, **kwargs})

    async def abort(self, job_id: str, *, timeout_seconds: float | None = None) -> bool:
        return False


def _found(*values: object) -> None:
    metadata_records.due_conversation_metadata_records.return_value = [
        MetadataRecord(conversation_id=uuid4(), value=value) for value in values
    ]


async def test_a_reply_owed_for_longer_than_a_day_is_dropped_not_sent():
    """Nobody is waiting on it any more, and it would land in a conversation
    that has moved on."""
    stale = OwedReply(
        conversation_id=uuid4(),
        agent_run_id=uuid4(),
        message="old news",
        next_at=NOW - GIVE_UP_AFTER - timedelta(minutes=1),
    )
    _found(stale.model_dump(mode="json"))
    queue = _Queue()

    swept = await sweep_owed_replies(_UowFactory(), now=NOW, job_queue=queue)

    assert (swept.requeued, swept.expired, queue.jobs) == (0, 1, [])
    forget = metadata_records.forget_conversation_metadata_record.await_args
    assert forget.kwargs["matching"] == {"agent_run_id": str(stale.agent_run_id)}


async def test_a_record_that_is_not_an_owed_reply_is_cleared():
    """Left alone it would be found, and fail to parse, on every run."""
    _found({"message": "no conversation id"})
    queue = _Queue()

    swept = await sweep_owed_replies(_UowFactory(), now=NOW, job_queue=queue)

    assert (swept.requeued, queue.jobs) == (0, [])
    forget = metadata_records.forget_conversation_metadata_record.await_args
    assert forget.kwargs["matching"] == {}
