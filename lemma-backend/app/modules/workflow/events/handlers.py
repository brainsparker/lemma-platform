"""The workflow module's remaining stream consumer and streaq task.

Function-step and AGENT-step resumes, timers, decisions, expiries and admission
are work items now (``run_work.py``, ``step_jobs.py``, ``agent_resume_work.py``).
What is left here moves in the next step of the cutover: schedule fires ride
``workflow-schedule-events`` until schedules write ``workflow.start``.
"""

from datetime import datetime

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    get_streaq_job_queue,
)
from app.core.infrastructure.jobs.streaq_runtime import (
    AppWorkerContext,
    streaq_task,
    streaq_worker,
)

from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.workflow.api.dependencies import build_workflow_engine
from app.modules.workflow.services.schedule_start_service import ScheduleStartService
from app.core.log.log import get_logger

logger = get_logger(__name__)

router = RedisRouter()


def provide_job_queue() -> SharedStreaqJobQueue:
    """Get the shared streaq job queue."""
    return get_streaq_job_queue()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


# --- Schedule Integration ---


@reliable_redis_stream_subscriber(
    router,
    "schedule_events",
    group="workflow-schedule-events",
    consumer="workflow-schedule-events-consumer",
)
async def handle_schedule_events(
    event: dict,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
):
    """Handle schedule events to launch workflows."""
    event_type = event.get("event_type")

    if event_type != "schedule.fired":
        return

    # Validate inside the inbox, not before it. The inbox turns a ValidationError
    # into a TERMINAL outcome and acks; raising out here instead would nack an
    # unparseable event forever, since the 60s reclaim subscriber has no attempt
    # cap and would redeliver the same poison message indefinitely.
    async def process() -> None:
        fired = ScheduleFired.model_validate(event)
        await on_schedule_fired(fired, fs_logger, job_queue)

    await inbox.process("workflow.schedule-start", event, process)


async def on_schedule_fired(
    event: ScheduleFired,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue,
):
    """Handle ScheduleFired: wake workflow waits or launch scheduled targets."""
    schedule_id = event.schedule_id
    source_occurred_at = event.scheduled_at or event.occurred_at
    schedule_event_id = event.source_event_id

    # Dedup redelivered schedule fires: streaq drops a duplicate enqueue while a
    # task with the same _job_id is still queued/running (its lock releases on
    # completion), which covers the common at-least-once redelivery window between
    # this handler receiving the event and ack-ing it. Durable idempotency across
    # the full window still rests on the run's unique constraint (workflow target)
    # and the Redis dedup key (agent target) inside handle_schedule_fired. Only set
    dedup_kwargs = {
        "_job_id": f"workflow-schedule-fire:{schedule_id}:{schedule_event_id}"
    }

    await job_queue.enqueue(
        "check_and_start_flows_for_schedule",
        schedule_id=str(schedule_id),
        # Stays None across the queue boundary: str(None) would arrive as the
        # literal "None" and blow up on UUID() instead of being recognised as
        # an owner-less legacy timer.
        user_id=str(event.user_id) if event.user_id else None,
        payload=event.payload,
        metadata=event.metadata or {},
        llm_output=event.llm_output,
        schedule_event_id=str(schedule_event_id),
        source_occurred_at=(
            source_occurred_at.isoformat()
            if isinstance(source_occurred_at, datetime)
            else str(source_occurred_at)
            if source_occurred_at
            else None
        ),
        **dedup_kwargs,
    )


@streaq_task(name="check_and_start_flows_for_schedule", max_tries=10)
async def check_and_start_flows_for_schedule(
    schedule_id: str,
    user_id: str | None,
    payload: dict,
    schedule_event_id: str,
    metadata: dict | None = None,
    llm_output: dict | None = None,
    source_occurred_at: str | None = None,
):
    """Check schedules and start or wake workflow runs."""
    worker_ctx: AppWorkerContext = streaq_worker.context

    async with worker_ctx.uow() as uow:
        service = ScheduleStartService(build_workflow_engine(uow))
        await service.handle_schedule_fired(
            schedule_id=schedule_id,
            user_id=user_id,
            payload=payload,
            metadata=metadata,
            llm_output=llm_output,
            schedule_event_id=schedule_event_id,
            source_occurred_at=(
                datetime.fromisoformat(source_occurred_at.replace("Z", "+00:00"))
                if source_occurred_at
                else None
            ),
        )
