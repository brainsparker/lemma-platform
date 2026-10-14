"""Finding owed replies whose queued attempt was lost, and queueing them again.

An owed reply is written into its conversation before its deferred attempt is
queued (``owed_reply``), and cleared when that attempt delivers it or gives up.
One still there long after it fell due is one whose job never ran -- lost with
the worker that held it, or never queued because the queue was down. This puts
it back on the queue.

Re-queueing can never post a reply twice: every attempt claims the reply in
Redis first and finds it marked sent if an earlier attempt landed it. The
sweep moves the record's due time to now as it re-queues, so a job that is
merely slow is not queued again on every run.

A reply owed for longer than a day is dropped instead: nobody is still waiting
on that answer, and it would arrive under a conversation that has moved on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from redis.exceptions import RedisError

from app.core.domain.job_queue import JobQueuePort
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversation_metadata_records as metadata_records,
)
from app.modules.agent.contracts.conversation_metadata_records import MetadataRecord
from app.modules.agent_surfaces.services.owed_reply import (
    OWED_REPLY_KEY,
    OwedReply,
    enqueue_owed_reply,
    forget_owed_reply,
    record_owed_reply,
)

logger = get_logger(__name__)

#: Records looked at per run. A sweep that is far behind catches up over a
#: few runs rather than holding the lane.
SWEEP_BATCH = 100
#: How far past due a record has to be before its job counts as lost: well
#: beyond a busy queue's lag, so a job that is only late is left to run.
STALE_AFTER = timedelta(minutes=15)
#: How long an answer is still worth delivering.
GIVE_UP_AFTER = timedelta(days=1)

_QUEUE_ERRORS: tuple[type[BaseException], ...] = (RedisError, OSError)


@dataclass(frozen=True, slots=True)
class OwedReplySweep:
    """What one run did."""

    requeued: int = 0
    expired: int = 0


async def sweep_owed_replies(
    uow_factory: UnitOfWorkFactory,
    *,
    now: datetime | None = None,
    job_queue: JobQueuePort | None = None,
) -> OwedReplySweep:
    """Re-queue every owed reply whose attempt was lost; drop the too-old."""
    at = now or datetime.now(UTC)
    async with uow_factory() as uow:
        records = await metadata_records.due_conversation_metadata_records(
            uow,
            key=OWED_REPLY_KEY,
            due_field="next_at",
            due_before=at - STALE_AFTER,
            limit=SWEEP_BATCH,
        )
    requeued = expired = 0
    for record in records:
        owed = await _owed(record, uow_factory)
        if owed is None:
            continue
        if owed.next_at is not None and owed.next_at < at - GIVE_UP_AFTER:
            logger.warning(
                "agent_surfaces.owed_reply_sweep.expired.degraded",
                conversation_id=str(owed.conversation_id),
                attempt=owed.attempt + 1,
            )
            await forget_owed_reply(owed, uow_factory=uow_factory)
            expired += 1
            continue
        if await _requeue(owed, at=at, uow_factory=uow_factory, job_queue=job_queue):
            requeued += 1
    return OwedReplySweep(requeued=requeued, expired=expired)


async def _owed(
    record: MetadataRecord, uow_factory: UnitOfWorkFactory
) -> OwedReply | None:
    """The record as an owed reply; one that is not is dropped, or it would
    be found again on every run."""
    try:
        return OwedReply.model_validate(record.value)
    except ValidationError:
        logger.warning(
            "agent_surfaces.owed_reply_sweep.unreadable.degraded",
            conversation_id=str(record.conversation_id),
            exc_info=True,
        )
        async with uow_factory() as uow:
            await metadata_records.forget_conversation_metadata_record(
                uow, record.conversation_id, OWED_REPLY_KEY, matching={}
            )
        return None


async def _requeue(
    owed: OwedReply,
    *,
    at: datetime,
    uow_factory: UnitOfWorkFactory,
    job_queue: JobQueuePort | None,
) -> bool:
    """Queue the lost attempt again, now. False when the queue refused it."""
    due_now = owed.model_copy(update={"next_at": at})
    await record_owed_reply(due_now, uow_factory=uow_factory)
    try:
        await enqueue_owed_reply(
            due_now,
            job_queue=job_queue,
            job_suffix=f":swept-{int(at.timestamp())}",
        )
    except _QUEUE_ERRORS:
        # The record is still there, due now: the next run tries again.
        logger.warning(
            "agent_surfaces.owed_reply_sweep.requeue_failed.degraded",
            conversation_id=str(owed.conversation_id),
            exc_info=True,
        )
        return False
    logger.warning(
        "agent_surfaces.owed_reply_sweep.requeued.degraded",
        conversation_id=str(owed.conversation_id),
        attempt=owed.attempt + 1,
    )
    return True


__all__ = ["OwedReplySweep", "sweep_owed_replies"]
