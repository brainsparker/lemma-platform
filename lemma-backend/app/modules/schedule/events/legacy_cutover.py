"""The cutover's converters for what the old transport owed the schedule module.

* ``schedule-notifications`` -- a paused schedule's notice, the item
  ``services/paused_notice`` writes today, keyed the same way.
* ``schedule.workflow-outcomes`` / ``schedule.agent-outcomes`` -- a target
  that ended: its outcome recorded on the schedule run, through the service the
  ``schedule.*_finished`` subscribers call. Recording is once per run.
* ``state:schedule.dispatched_runs`` -- once, at the end: every run still
  DISPATCHED whose target has ended, however its outcome got lost, and every
  agent run whose conversation is gone dead-lettered -- the job of the
  five-minute recovery sweep this replaces, which outcomes arriving as facts
  (written with the target's terminal status) made redundant from here on.
* ``streaq:handle_llm_filter_task`` -- a filter job only Redis held, as the
  ``schedule.filter`` item a fire writes today.

Worker only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from app.core.infrastructure.cutover.routes import legacy_converter
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import JobContext, WorkWriter
from app.modules.schedule.work import registration as _registration  # noqa: F401  registers the kinds written here
from app.modules.schedule.work.kinds import (
    SCHEDULE_FILTER,
    SCHEDULE_PAUSED_NOTICE,
    FilterEvent,
    PausedNotice,
    schedule_key,
    schedule_namespace,
)

#: A target's terminal status -> the schedule run's (as ``work/outcome``).
_OUTCOMES = {
    "COMPLETED": "COMPLETED",
    "SUCCEEDED": "COMPLETED",
    "FAILED": "TARGET_FAILED",
    "CANCELLED": "CANCELLED",
    "STOPPED": "CANCELLED",
}
_BATCH = 200


async def _record(
    uow: SqlAlchemyUnitOfWork, target_kind: str, target_run_id: str, status: str
) -> bool:
    from app.modules.schedule.domain.schedule import ScheduleRunStatus
    from app.modules.schedule.services.run_outcome_service import (
        ScheduleRunOutcomeService,
    )

    mapped = _OUTCOMES.get(status)
    if mapped is None:
        return False
    run_status = ScheduleRunStatus(mapped)
    return await ScheduleRunOutcomeService(uow).record_target_outcome(
        target_kind=target_kind,
        target_run_id=target_run_id,
        status=run_status,
        completed_at=datetime.now(UTC),
        error_type=(
            f"{target_kind.title()}RunFailed"
            if run_status is ScheduleRunStatus.TARGET_FAILED
            else None
        ),
    )


def _text(status: object) -> str:
    return str(getattr(status, "value", status))


@legacy_converter("schedule-notifications")
async def paused_notice(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    del route
    pod_id = UUID(str(event["pod_id"])) if event.get("pod_id") else None
    reason = str(event.get("reason") or "consecutive_failures")
    schedule_id = UUID(str(event["schedule_id"]))
    async with ctx.complete_work() as uow:
        await WorkWriter.of(uow).add(
            SCHEDULE_PAUSED_NOTICE,
            schedule_namespace(pod_id),
            PausedNotice(
                schedule_id=schedule_id,
                user_id=UUID(str(event["user_id"])),
                reason=reason,
                consecutive_failures=int(event.get("consecutive_failures") or 0),
            ),
            dedup_key=f"schedule-paused:{schedule_id}:{reason}",
        )


@legacy_converter("schedule.workflow-outcomes")
async def workflow_outcome(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    del route
    async with ctx.complete_work() as uow:
        await _record(uow, "WORKFLOW", str(event["run_id"]), str(event["status"]))


@legacy_converter("schedule.agent-outcomes")
async def agent_outcome(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    """The old consumer read the conversation's state, not the event's: a
    conversation outlives a run, and a run that ended on a pause is not it
    finishing."""
    del route
    from app.modules.agent.contracts.conversation_outcomes import (
        load_conversation_outcome,
    )

    conversation_id = UUID(str(event["conversation_id"]))
    async with ctx.complete_work() as uow:
        outcome = await load_conversation_outcome(uow, conversation_id)
        if outcome is not None:
            await _record(uow, "AGENT", str(conversation_id), _text(outcome.status))


@legacy_converter("state:schedule.dispatched_runs")
async def dispatched_runs(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    """Every DISPATCHED run whose target has ended, recorded; in batches."""
    del event, route
    from app.modules.agent.contracts.conversation_outcomes import (
        load_conversation_outcomes,
    )
    from app.modules.schedule.infrastructure.models.run import ScheduleRun
    from app.modules.schedule.services.target_start import dead_letter_run

    after = UUID(int=0)
    while True:
        ctx.raise_if_stopping()
        async with ctx.checkpoint() as uow:
            rows = (
                await uow.session.execute(
                    select(
                        ScheduleRun.id,
                        ScheduleRun.target_kind,
                        ScheduleRun.target_run_id,
                    )
                    .where(
                        ScheduleRun.status == "DISPATCHED",
                        ScheduleRun.target_outcome.is_(None),
                        ScheduleRun.target_run_id.is_not(None),
                        ScheduleRun.id > after,
                    )
                    .order_by(ScheduleRun.id)
                    .limit(_BATCH)
                )
            ).all()
            if not rows:
                return
            after = rows[-1].id
            agents = {
                UUID(str(row.target_run_id)): str(row.target_run_id)
                for row in rows
                if row.target_kind == "AGENT"
            }
            workflows = [
                str(row.target_run_id) for row in rows if row.target_kind == "WORKFLOW"
            ]
            outcomes = await load_conversation_outcomes(uow, set(agents))
            ended: list[tuple[str, str, str]] = [
                ("AGENT", target, _text(outcomes[conversation_id].status))
                for conversation_id, target in agents.items()
                if conversation_id in outcomes
            ]
            # A conversation that is gone will never end its run.
            gone = [
                row.id
                for row in rows
                if row.target_kind == "AGENT"
                and UUID(str(row.target_run_id)) not in outcomes
            ]
            for run_id in gone:
                await dead_letter_run(uow, run_id, error_type="ScheduleTargetMissing")
            if workflows:
                statuses = await uow.session.execute(
                    text(
                        "SELECT id::text, status FROM workflow_flow_runs "
                        "WHERE id::text = ANY(:ids)"
                    ),
                    {"ids": workflows},
                )
                ended += [("WORKFLOW", run_id, status) for run_id, status in statuses]
            for target_kind, target_run_id, status in ended:
                await _record(uow, target_kind, target_run_id, status)


@legacy_converter("streaq:handle_llm_filter_task")
async def llm_filter(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    del route
    from app.modules.schedule.contracts.dispatch import get_schedule

    kwargs = dict(event.get("kwargs") or {})
    schedule_id = kwargs.get("schedule_id")
    source_event_id = kwargs.get("source_event_id")
    if not schedule_id or not source_event_id:
        return
    async with ctx.complete_work() as uow:
        schedule = await get_schedule(uow, UUID(str(schedule_id)))
        if schedule is None:
            return
        await WorkWriter.of(uow).add(
            SCHEDULE_FILTER,
            schedule_namespace(schedule.pod_id),
            FilterEvent(
                schedule_id=schedule.id,
                source_event_id=str(source_event_id),
                payload=dict(kwargs.get("payload") or {}),
                metadata=dict(kwargs.get("metadata") or {}),
                user_id=UUID(str(kwargs["user_id"])) if kwargs.get("user_id") else None,
            ),
            dedup_key=f"filter:{schedule.id}:{source_event_id}",
            concurrency_key=schedule_key(schedule.id),
            rate_key=schedule_key(schedule.id),
        )
