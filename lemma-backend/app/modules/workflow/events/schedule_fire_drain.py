"""``workflow.drain_schedule_fires``: the cutover for schedule fires still in flight.

Before PR 2, a TIME schedule fired by putting ``schedule.fired`` on the
``schedule_events`` stream; the ``workflow-schedule-events`` group turned each
into a streaq job (``check_and_start_flows_for_schedule``) that started the
target. Schedules write ``schedule.fire`` items now, and nothing publishes
``schedule.fired`` any more -- but what the old path held when it stopped is a
fire that exists nowhere else (design §13, rule 2). This converts it:

* the group's unfinished entries (its pending list and anything unread);
* unpublished ``schedule.fired`` outbox rows;
* queued ``check_and_start_flows_for_schedule`` streaq jobs.

Each becomes the start the job would have made, in its own transaction, through
the same ``ScheduleStartService.handle_schedule_fired`` -- whose claim of the
schedule run is unique per (schedule, occurrence), so a fire reached twice (an
old worker finishing the same entry during the rolling deploy) starts once.
The group is the converter's cursor: an entry is acknowledged after its start
commits. Seeded once by migration 0062; it keeps looking for ``QUIET_FOR``
after the last thing it converted (old workers may still hand a job back to
Redis while they stop), then destroys the group and finishes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text

from app.core.infrastructure.cutover.legacy_streams import (
    decode_entry,
    destroy_group,
    read_group,
)
from app.core.infrastructure.cutover.streaq_jobs import forget_job, queued_jobs
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

DRAIN_SCHEDULE_FIRES = "workflow.drain_schedule_fires"
STREAM = "schedule_events"
GROUP = "workflow-schedule-events"
CONSUMER = "cutover-drain"
FIRED = "schedule.fired"
STREAQ_FUNCTION = "check_and_start_flows_for_schedule"
BATCH = 200
IDLE_DELAY = timedelta(seconds=10)
#: Longer than an old worker's shutdown grace, so a job it hands back to its
#: queue while stopping is still found.
QUIET_FOR = timedelta(minutes=10)


class DrainState(BaseModel):
    #: When a pass last found nothing to convert (None: it just converted).
    quiet_since: datetime | None = None


async def run_drain(ctx: JobContext, payload: DrainState) -> None:
    redis = get_redis(decode_responses=False)
    converted = 0
    while True:
        ctx.raise_if_stopping()
        batch = await _drain_group(redis) + await _drain_outbox()
        converted += batch
        if batch == 0:
            break
    converted += await _drain_streaq(redis)
    now = datetime.now(UTC)
    if converted:
        logger.info("workflow.schedule_fire_drain.converted", converted=converted)
        state = DrainState()
    else:
        state = DrainState(quiet_since=payload.quiet_since or now)
    if state.quiet_since is not None and now - state.quiet_since >= QUIET_FOR:
        await destroy_group(redis, STREAM, GROUP)
        logger.info("workflow.schedule_fire_drain.finished")
        return
    async with ctx.complete_work() as uow:
        await ctx.writer(uow).add(
            DRAIN_SCHEDULE_FIRES,
            SYSTEM_NAMESPACE,
            state,
            dedup_key=DRAIN_SCHEDULE_FIRES,
            delay=IDLE_DELAY,
        )


async def start_fire(event: dict[str, Any]) -> bool:
    """Start what one ``schedule.fired`` would have started; False if it is not one."""
    if event.get("event_type") != FIRED or not event.get("schedule_id"):
        return False
    occurred = event.get("scheduled_at") or event.get("occurred_at")
    await start_legacy_job(
        schedule_id=str(event["schedule_id"]),
        user_id=event.get("user_id"),
        payload=event.get("payload") or {},
        metadata=event.get("metadata") or {},
        llm_output=event.get("llm_output"),
        schedule_event_id=str(event.get("source_event_id") or ""),
        source_occurred_at=str(occurred) if occurred else None,
    )
    return True


async def start_legacy_job(
    *,
    schedule_id: str,
    user_id: str | None,
    payload: dict[str, Any],
    schedule_event_id: str,
    metadata: dict[str, Any] | None = None,
    llm_output: dict[str, Any] | None = None,
    source_occurred_at: str | None = None,
) -> None:
    """The body of the old ``check_and_start_flows_for_schedule`` job."""
    from app.modules.workflow.api.dependencies import build_workflow_engine
    from app.modules.workflow.services.schedule_start_service import (
        ScheduleStartService,
    )

    async with SqlAlchemyUnitOfWork(async_session_maker()) as uow:
        await ScheduleStartService(build_workflow_engine(uow)).handle_schedule_fired(
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
        await uow.commit()


async def _drain_group(redis: Any) -> int:
    entries = await read_group(redis, STREAM, GROUP, CONSUMER, count=BATCH)
    for entry_id, fields in entries:
        event = decode_entry(fields)
        if event is not None:
            await start_fire(event)
        await redis.xack(STREAM, GROUP, entry_id)
    return len(entries)


async def _drain_outbox() -> int:
    async with async_session_maker() as session:
        if not await session.scalar(
            text("SELECT to_regclass('domain_event_outbox') IS NOT NULL")
        ):
            return 0
        rows = (
            await session.execute(
                text(
                    "SELECT id, payload FROM domain_event_outbox "
                    "WHERE event_type = :type AND published_at IS NULL "
                    "AND dead_lettered_at IS NULL ORDER BY occurred_at LIMIT :limit"
                ),
                {"type": FIRED, "limit": BATCH},
            )
        ).all()
    for row_id, event in rows:
        if isinstance(event, dict):
            await start_fire(event)
        async with async_session_maker() as session:
            await session.execute(
                text(
                    "UPDATE domain_event_outbox SET published_at = now() WHERE id = :id"
                ),
                {"id": row_id},
            )
            await session.commit()
    return len(rows)


async def _drain_streaq(redis: Any) -> int:
    converted = 0
    async for job in queued_jobs(redis):
        if job.function != STREAQ_FUNCTION:
            continue
        kwargs = job.kwargs
        await start_legacy_job(
            schedule_id=str(kwargs["schedule_id"]),
            user_id=kwargs.get("user_id"),
            payload=kwargs.get("payload") or {},
            metadata=kwargs.get("metadata"),
            llm_output=kwargs.get("llm_output"),
            schedule_event_id=str(kwargs.get("schedule_event_id") or ""),
            source_occurred_at=kwargs.get("source_occurred_at"),
        )
        await forget_job(redis, job)
        converted += 1
    return converted


def register_drain_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=DRAIN_SCHEDULE_FIRES,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=DrainState,
        lease=timedelta(minutes=2),
    )(run_drain)


register_drain_work(default_registry)
