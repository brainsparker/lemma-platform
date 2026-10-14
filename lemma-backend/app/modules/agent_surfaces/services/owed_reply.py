"""A final answer the platform would not take yet, sent again later.

The answer is sent once, from the run's observer, and the inline retry around
that send waits out short rate limits only (``platforms/delivery``). A platform
asking for longer -- a Telegram group's 429 with ``retry_after`` in minutes, a
Slack channel limit, an outage -- used to lose the answer: every attempt was
spent in a second and a half, and nothing ever tried again.

So a reply the platform refused for a *transient* reason, with no part of it
delivered, is owed: written into a deferred worker job that carries the text
and the run whose held files ride it, and tried again after the wait the
platform asked for. Each attempt claims the reply in Redis first, and a
delivered reply is marked sent there, so a redelivered job never posts it
twice.

A queued job alone does not survive the worker that holds it: a deferred job
lost with its worker took the answer with it. So the debt is also written into
the conversation's metadata (``surface_owed_reply``) *before* the job is
queued -- the text, the attempt, and when it falls due -- and cleared once the
reply lands or is given up. A sweep (``owed_reply_sweep``) re-queues any that
fell due long ago and was never paid; the claim above keeps that from ever
posting it twice.

What is not covered, on purpose: a reply that was partly delivered (sending it
again repeats the part the person has), a permanent refusal (it will be refused
again), and a worker that dies between the run ending and the observer's send
(there is no record yet to resend from).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.core.domain.job_queue import JobQueuePort
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts import (
    conversation_metadata_records as metadata_records,
)
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.caches import OWED_REPLY_CLAIMS, surface_caches
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.delivery_limits import OWED_REPLY_ATTEMPTS
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.delivery import SendOutcome, track_sends
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress
from app.modules.agent_surfaces.services.pending_envelope import (
    RunFiles,
    discard_display_paths,
)

logger = get_logger(__name__)

DELIVER_OWED_REPLY_JOB = "deliver_owed_reply"

#: Waits before each deferred attempt, in seconds, one per attempt in
#: ``OWED_REPLY_ATTEMPTS``. A ``Retry-After`` longer than the step is honoured
#: instead. Together they span past an ordinary outage and stop well short of
#: an answer nobody still wants.
_RETRY_DELAYS_SECONDS = (30.0, 300.0, 1800.0)[:OWED_REPLY_ATTEMPTS]

#: How long an attempt holds the reply. A worker that dies mid-send leaves the
#: claim to expire, and the next attempt finds it free.
_SENDING_TTL_SECONDS = 600
#: How long a delivered reply is remembered as sent: longer than every attempt
#: and any redelivery of one.
_SENT_TTL_SECONDS = 2 * 24 * 60 * 60

_SENDING = "sending"
_SENT = "sent"


#: What one attempt can fail with and still leave the reply owed: the platform
#: family, and the database work that resolves where the reply goes.
def _attempt_errors() -> tuple[type[BaseException], ...]:
    return (SQLAlchemyError, *platform_transport_errors())


def _claims() -> RedisJsonCache:
    return surface_caches.get(OWED_REPLY_CLAIMS)


#: The conversation metadata key the debt is written under.
OWED_REPLY_KEY = "surface_owed_reply"


class OwedReply(BaseModel):
    """One final answer still to be delivered.

    The job's payload, and also what is written under ``OWED_REPLY_KEY``.
    """

    conversation_id: UUID
    agent_run_id: UUID | None
    message: str
    #: Deferred attempts already made; the inline send is not counted.
    attempt: int = 0
    #: When the attempt it is queued for falls due. What the sweep reads.
    next_at: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.conversation_id}:{self.agent_run_id or 'no-run'}"

    def identity(self) -> dict[str, str | None]:
        """What tells this reply from a later one owed in the same conversation."""
        return {"agent_run_id": str(self.agent_run_id) if self.agent_run_id else None}


def owes_a_retry(outcome: SendOutcome) -> bool:
    """Whether a failed send left a reply worth sending again later."""
    return outcome.gave_up_transient and not outcome.partial


def _default_queue() -> JobQueuePort:
    from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue

    return get_streaq_job_queue()


async def owe_reply(
    owed: OwedReply,
    *,
    retry_after: float | None,
    uow_factory: UnitOfWorkFactory,
    job_queue: JobQueuePort | None = None,
) -> bool:
    """Schedule the next attempt at an owed reply. False when none is left.

    Written down first and queued second: a job that is then lost leaves a
    record the sweep finds, where the other order could leave a queued job
    and no record -- or, had the queue failed, neither.
    """
    if owed.attempt >= len(_RETRY_DELAYS_SECONDS):
        return False
    delay = max(_RETRY_DELAYS_SECONDS[owed.attempt], retry_after or 0.0)
    scheduled = owed.model_copy(
        update={"next_at": datetime.now(UTC) + timedelta(seconds=delay)}
    )
    await record_owed_reply(scheduled, uow_factory=uow_factory)
    await enqueue_owed_reply(scheduled, job_queue=job_queue)
    logger.info(
        "agent_surfaces.owed_reply.scheduled",
        conversation_id=str(owed.conversation_id),
        attempt=owed.attempt + 1,
        delay_seconds=delay,
    )
    return True


async def enqueue_owed_reply(
    owed: OwedReply, *, job_queue: JobQueuePort | None = None, job_suffix: str = ""
) -> None:
    """Queue the attempt ``owed`` names, for when it falls due.

    ``job_suffix`` gives a re-queue its own job id, so the queue does not take
    it for the lost attempt whose id it may still remember.
    """
    await (job_queue or _default_queue()).enqueue(
        DELIVER_OWED_REPLY_JOB,
        payload=owed.model_dump(mode="json"),
        _job_id=f"owed-reply:{owed.key}:{owed.attempt}{job_suffix}",
        _defer_until=owed.next_at or datetime.now(UTC),
    )


async def record_owed_reply(owed: OwedReply, *, uow_factory: UnitOfWorkFactory) -> None:
    """Write the debt into the conversation. Never raises: the queued job
    still delivers it, only without a record to outlive the worker by."""
    try:
        async with uow_factory() as uow:
            await agent_conversations.set_conversation_metadata_value(
                uow, owed.conversation_id, OWED_REPLY_KEY, owed
            )
    except SQLAlchemyError:
        logger.warning(
            "agent_surfaces.owed_reply.not_recorded.degraded",
            conversation_id=str(owed.conversation_id),
            exc_info=True,
        )


async def forget_owed_reply(owed: OwedReply, *, uow_factory: UnitOfWorkFactory) -> None:
    """Clear the debt once paid or given up. Never raises: a record left
    behind is re-queued by the sweep, whose attempt then finds it sent."""
    try:
        async with uow_factory() as uow:
            await metadata_records.forget_conversation_metadata_record(
                uow, owed.conversation_id, OWED_REPLY_KEY, matching=owed.identity()
            )
    except SQLAlchemyError:
        logger.warning(
            "agent_surfaces.owed_reply.not_forgotten.degraded",
            conversation_id=str(owed.conversation_id),
            exc_info=True,
        )


async def deliver_owed_reply(
    owed: OwedReply,
    *,
    uow_factory: UnitOfWorkFactory,
    egress_factory: Callable[[SqlAlchemyUnitOfWork], SurfaceEgress],
    job_queue: JobQueuePort | None = None,
) -> None:
    """One deferred attempt: send the reply once, or owe it again."""
    claims = _claims()
    if await claims.get_raw(owed.key) == _SENT:
        # Sent by an earlier attempt whose record outlived it -- a clear that
        # failed, or a re-queue from the sweep. The record is all that is left.
        await forget_owed_reply(owed, uow_factory=uow_factory)
        return
    if not await claims.set_raw_if_absent(
        owed.key, _SENDING, ttl_seconds=_SENDING_TTL_SECONDS
    ):
        # Another attempt holds it. It either delivers, or gives the claim
        # back and owes the reply again itself, so there is nothing to add.
        return
    run = RunFiles(owed.agent_run_id)
    with track_sends() as outcome:
        try:
            async with uow_factory() as uow:
                delivered = await egress_factory(
                    uow
                ).send_agent_message_for_conversation(
                    conversation_id=owed.conversation_id,
                    message=owed.message,
                    attach_files_of=run,
                )
        except _attempt_errors():
            logger.warning(
                "agent_surfaces.owed_reply.attempt_failed.degraded",
                conversation_id=str(owed.conversation_id),
                attempt=owed.attempt + 1,
                exc_info=True,
            )
            delivered = False
    if delivered:
        await claims.set_raw(owed.key, _SENT, ttl_seconds=_SENT_TTL_SECONDS)
        await forget_owed_reply(owed, uow_factory=uow_factory)
        await discard_display_paths(owed.conversation_id, run)
        logger.info(
            "agent_surfaces.owed_reply.delivered",
            conversation_id=str(owed.conversation_id),
            attempt=owed.attempt + 1,
        )
        return
    await claims.delete_if_value(owed.key, _SENDING)
    retried = owes_a_retry(outcome) and await owe_reply(
        owed.model_copy(update={"attempt": owed.attempt + 1}),
        retry_after=outcome.retry_after,
        uow_factory=uow_factory,
        job_queue=job_queue,
    )
    if not retried:
        # An error, not a warning: this is an answer the person will never get.
        logger.error(
            "agent_surfaces.owed_reply.abandoned.failed",
            conversation_id=str(owed.conversation_id),
            attempts=owed.attempt + 1,
            partial=outcome.partial,
        )
        await forget_owed_reply(owed, uow_factory=uow_factory)
        await discard_display_paths(owed.conversation_id, run)
