"""The workflow module's remaining stream consumers, streaq tasks and crons.

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
    streaq_cron,
    streaq_task,
    streaq_worker,
)

from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.workflow.api.dependencies import build_workflow_engine
from app.modules.workflow.services.schedule_start_service import ScheduleStartService
from app.core.log.log import get_logger
from app.modules.workflow.config import workflow_settings

logger = get_logger(__name__)

router = RedisRouter()


def provide_job_queue() -> SharedStreaqJobQueue:
    """Get the shared streaq job queue."""
    return get_streaq_job_queue()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@streaq_cron("41 * * * *", name="prune_workflow_run_waits")
async def prune_workflow_run_waits() -> None:
    """Reclaim finished machine waits. Human approvals are never touched.

    A wait row is scaffolding for one step: the engine records what a run is
    blocked on, and once the function returns or the agent finishes, the row has
    served its purpose. Nothing removed them, so they accumulated -- in
    production, roughly 105,000 machine waits against 5,700 human ones.

    ``HUMAN`` waits are excluded by predicate at any age and any status. Those
    are the record of who was asked to approve something and what they said,
    which is not scaffolding and not ours to age out. If that distinction ever
    stops holding, this sweep is the thing that must change, not the retention
    window.

    Offset off the hour to stay clear of the other delete-heavy sweeps.
    """
    from app.modules.workflow.infrastructure.repositories.wait_retention import (
        prune_terminal_machine_waits,
    )

    deleted = await prune_terminal_machine_waits(
        async_session_maker,
        retention_days=workflow_settings.workflow_wait_retention_days,
        batch_size=workflow_settings.workflow_wait_retention_batch_size,
        budget_seconds=workflow_settings.workflow_wait_retention_budget_seconds,
    )
    if deleted:
        logger.debug(
            "workflow.handlers.prune_workflow_run_waits.observed",
            deleted_count=deleted,
        )


@streaq_cron("3-59/5 * * * *", name="expire_past_due_notifications")
async def expire_past_due_notifications():
    """Close out notifications nobody answered before their deadline.

    Not a failure — people are busy, and the deadline is 72h precisely so that
    ordinary out-of-hours delay does not trip it. But a row that stays OPEN
    forever is an inbox badge that never clears and an asking run waiting on
    something that will never arrive.

    Lives beside the two wait sweeps deliberately: same cadence, same batch
    discipline, same "the timer may have been lost, the row is the truth" shape.
    A third invention here would drift from the other two.
    """
    from app.modules.agent_surfaces.contracts.workflow_notifications import (
        expire_past_due_notifications,
    )

    worker_ctx: AppWorkerContext = streaq_worker.context
    async with worker_ctx.uow() as uow:
        expired = await expire_past_due_notifications(uow)
        if expired:
            logger.info(
                "agent_surfaces.notifications.expired.observed",
                count=expired,
            )
        await uow.commit()


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


@streaq_cron("4-59/5 * * * *", name="recover_schedule_runs")
async def recover_schedule_runs() -> None:
    from app.modules.schedule.contracts.run_recovery import recover_schedule_runs

    worker_ctx: AppWorkerContext = streaq_worker.context
    async with worker_ctx.uow() as uow:
        result = await recover_schedule_runs(uow)
    # Still a warning, and deliberately so. This used to fire twelve times an
    # hour and was read as routine maintenance, but that was the counting: rows
    # the sweep inspected and correctly left alone were reported as
    # `reconciled`, so a sweep doing nothing at all looked busy. Now the three
    # counters below only move when the event-driven outcome path missed
    # something, a dispatch was lost, or a run was abandoned -- each of which is
    # the safety net catching a failure somewhere else, and worth a warning.
    if result.redelivered or result.reconciled or result.dead_lettered:
        logger.warning(
            "schedule.runs.recovered",
            redelivered=result.redelivered,
            reconciled=result.reconciled,
            dead_lettered=result.dead_lettered,
            still_running=result.still_running,
        )
