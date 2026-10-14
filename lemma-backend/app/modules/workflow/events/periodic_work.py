"""The workflow module's periodic work.

* ``workflow.waits_prune`` (1 h, admin) -- finished machine waits.
* ``workflow.schedule_runs_recover`` (5 min, automation) -- schedule fires the
  event path lost. Goes when schedules write ``workflow.start`` in the fire's
  own transaction; until then it is the backstop for that stream.

Worker only (``register_periodic_work``).
"""

from __future__ import annotations

from datetime import timedelta

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import WorkClass, periodic_job
from app.core.log.log import get_logger
from app.modules.workflow.config import workflow_settings

logger = get_logger(__name__)


@periodic_job("workflow.waits_prune", every=timedelta(hours=1))
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


# Automation: it redelivers schedule fires the event path lost.
@periodic_job(
    "workflow.schedule_runs_recover",
    every=timedelta(minutes=5),
    cls=WorkClass.AUTOMATION,
)
async def recover_schedule_runs() -> None:
    from app.modules.schedule.contracts.run_recovery import recover_schedule_runs

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
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
