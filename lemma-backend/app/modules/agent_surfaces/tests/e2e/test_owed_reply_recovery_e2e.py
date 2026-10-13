"""An owed reply whose queued attempt was lost is still delivered, once.

A final answer the platform refused for now is owed: retried later by a deferred
job. A worker that died holding that job used to take the answer with it. The
debt is now written into the conversation before the job is queued, and a sweep
re-queues one that fell due long ago and was never paid.

Driven against Postgres and Redis: the job is "lost" by a queue that accepts it
and never runs it, the sweep finds the record, and the re-queued attempt -- run
alongside a late copy of the lost one -- puts the answer on the platform once.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.infrastructure.repositories.conversation_repository import (
    ConversationRepository,
)
from app.modules.agent_surfaces.composition import build_surface_egress
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.services.owed_reply import (
    OWED_REPLY_KEY,
    OwedReply,
    deliver_owed_reply,
    owe_reply,
)
from app.modules.agent_surfaces.services.owed_reply_sweep import (
    STALE_AFTER,
    sweep_owed_replies,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import script_text
from app.modules.agent_surfaces.tests.e2e.surface_journey import stage_surface

pytestmark = pytest.mark.e2e

LATE_ANSWER = "Here is the answer the platform would not take earlier."


class _Queue:
    """Accepts every job and runs none: a queue whose worker went away."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    async def enqueue(self, job_name: str, **kwargs: Any) -> None:
        self.jobs.append({"name": job_name, **kwargs})

    async def abort(self, job_id: str, *, timeout_seconds: float | None = None) -> bool:
        return False


async def _record(uow_factory, conversation_id: UUID) -> object:
    async with uow_factory() as uow:
        return await ConversationRepository(uow).get_conversation_metadata_key(
            conversation_id, OWED_REPLY_KEY
        )


async def test_a_lost_owed_reply_is_swept_back_and_delivered_once(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    message_store,
    monkeypatch,
    platform_fake,
) -> None:
    stage = await stage_surface(
        SurfacePlatform.TELEGRAM,
        fake=platform_fake[SurfacePlatform.TELEGRAM],
        authenticated_client=authenticated_client,
        db_session=db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fixed_test_org=fixed_test_org,
        message_store=message_store,
        monkeypatch=monkeypatch,
    )
    context = await stage.say("hello", script=[script_text("Hi there.")])
    assert await stage.saw("Hi there.")
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    conversation_id = context.conversation_id

    # Owed, and its job lost with the worker that held it.
    lost = _Queue()
    assert await owe_reply(
        OwedReply(
            conversation_id=conversation_id, agent_run_id=None, message=LATE_ANSWER
        ),
        retry_after=None,
        uow_factory=uow_factory,
        job_queue=lost,
    )
    [lost_job] = lost.jobs
    record = await _record(uow_factory, conversation_id)
    assert isinstance(record, dict), "the debt was queued but never written down"
    assert record["message"] == LATE_ANSWER

    # Not yet stale: a job that is only late is left alone.
    owed = OwedReply.model_validate(record)
    assert owed.next_at is not None
    sweeper = _Queue()
    early = await sweep_owed_replies(uow_factory, now=owed.next_at, job_queue=sweeper)
    assert (early.requeued, sweeper.jobs) == (0, [])

    swept = await sweep_owed_replies(
        uow_factory,
        now=owed.next_at + STALE_AFTER + timedelta(minutes=1),
        job_queue=sweeper,
    )
    assert swept.requeued == 1
    [requeued] = sweeper.jobs
    assert requeued["_job_id"] != lost_job["_job_id"]

    # The re-queued attempt runs, and so -- late -- does the lost one.
    for job in (requeued, lost_job):
        await deliver_owed_reply(
            OwedReply.model_validate(job["payload"]),
            uow_factory=uow_factory,
            egress_factory=build_surface_egress,
        )

    sent = [
        message
        for message in message_store.get_all("TELEGRAM")
        if LATE_ANSWER in str(message.get("text") or "")
    ]
    assert len(sent) == 1, f"the owed reply arrived {len(sent)} times"
    assert await _record(uow_factory, conversation_id) is None, (
        "a delivered reply left its record behind"
    )

    # Paid: the next run finds nothing to re-queue.
    again = await sweep_owed_replies(
        uow_factory,
        now=owed.next_at + timedelta(hours=1),
        job_queue=sweeper,
    )
    assert again.requeued == 0
    assert len(sweeper.jobs) == 1
