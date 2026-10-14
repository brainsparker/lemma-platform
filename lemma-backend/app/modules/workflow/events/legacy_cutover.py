"""The cutover's converter for schedule fires the old transport still held.

Before the work runtime a TIME schedule fired by putting ``schedule.fired`` on
the ``schedule_events`` stream, and the ``workflow-schedule-events`` group
turned each into a streaq job (``check_and_start_flows_for_schedule``) that
started the target. A fire still owed in the outbox, or still a job in Redis,
exists nowhere else (design §13 rule 2): its schedule's cursor has already
moved past it. Each starts the target the job would have started, through
``ScheduleStartService.start_for_fire``, whose claim of the schedule run is
unique per (schedule, occurrence) -- a fire reached twice starts once. Worker
only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.infrastructure.cutover.routes import legacy_converter
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import JobContext


async def start_legacy_fire(
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
        await ScheduleStartService(build_workflow_engine(uow)).start_for_fire(
            schedule_id=schedule_id,
            user_id=user_id,
            payload=payload,
            schedule_event_id=schedule_event_id,
            metadata=metadata,
            llm_output=llm_output,
            source_occurred_at=(
                datetime.fromisoformat(source_occurred_at.replace("Z", "+00:00"))
                if source_occurred_at
                else None
            ),
        )
        await uow.commit()


def _is_timer_wake(payload: dict[str, Any]) -> bool:
    """A timer fire for something already waiting: a suspended workflow run's
    (now a ``workflow.resume`` item) or a waiting conversation's (an
    ``agent.wait.fire`` item). Migrations 0057 and 0060 wrote one for every
    wait that was active, so the old fire is dropped, not routed."""
    return bool(payload.get("conversation_id") or payload.get("workflow_run_id"))


@legacy_converter("workflow.schedule-start")
async def schedule_fired(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    del ctx, route
    if not event.get("schedule_id") or _is_timer_wake(event.get("payload") or {}):
        return
    occurred = event.get("scheduled_at") or event.get("occurred_at")
    await start_legacy_fire(
        schedule_id=str(event["schedule_id"]),
        user_id=event.get("user_id"),
        payload=event.get("payload") or {},
        metadata=event.get("metadata") or {},
        llm_output=event.get("llm_output"),
        schedule_event_id=str(event.get("source_event_id") or ""),
        source_occurred_at=str(occurred) if occurred else None,
    )


@legacy_converter("streaq:check_and_start_flows_for_schedule")
async def schedule_job(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    del ctx, route
    kwargs = dict(event.get("kwargs") or {})
    if not kwargs.get("schedule_id") or _is_timer_wake(kwargs.get("payload") or {}):
        return
    await start_legacy_fire(
        schedule_id=str(kwargs["schedule_id"]),
        user_id=kwargs.get("user_id"),
        payload=kwargs.get("payload") or {},
        metadata=kwargs.get("metadata"),
        llm_output=kwargs.get("llm_output"),
        schedule_event_id=str(kwargs.get("schedule_event_id") or ""),
        source_occurred_at=kwargs.get("source_occurred_at"),
    )
