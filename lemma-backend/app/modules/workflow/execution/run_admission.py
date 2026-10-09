"""Holding runs back to a workflow's max_concurrent_runs, and letting them in.

A run started while its workflow is at the limit is not refused -- a schedule or
a webhook burst has nobody to tell -- and not dropped. It is created as usual
and parked at its entry node on a ``QUEUED`` wait, which does not count towards
the limit. Whenever a run of the workflow finishes, the oldest parked runs are
admitted into the places that frees, and the reconciliation sweep does the same
for any finish whose admission was lost.

Both sides lock the workflow row, so two starts cannot both take the last place
and two finishes cannot both admit into it.

Beside the engine rather than inside it for the reason ``wait_failure.py``
gives: the engine is at the architecture ratchet's file-size mark, and this is
part of its own implementation, not a collaborator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.workflow.domain.run import WorkflowRunEntity, WorkflowRunStatus
from app.modules.workflow.domain.wait import (
    WaitRequest,
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.execution.stepper import StepResult

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from app.modules.workflow.execution.engine import WorkflowEngine

logger = get_logger(__name__)

#: How many parked runs one admission pass takes when no limit bounds it --
#: a limit that was removed while runs were parked.
UNBOUNDED_ADMISSION_BATCH = 100


async def queue_wait_if_full(
    engine: "WorkflowEngine", flow: WorkflowEntity, run: WorkflowRunEntity
) -> WaitRequest | None:
    """The wait that parks a new run, or None when it may start now."""
    if flow.max_concurrent_runs is None:
        return None
    # Locked so two starts cannot both see the last free place.
    await engine.flow_repo.get_for_update(flow.id)
    admitted = await engine.run_repo.count_admitted(flow.id, excluding=run.id)
    if admitted < flow.max_concurrent_runs:
        return None
    return WaitRequest(wait_type=WorkflowRunWaitType.QUEUED, external_ref=str(flow.id))


async def admit_queued_runs(engine: "WorkflowEngine", flow_id: UUID) -> int:
    """Start as many parked runs of a workflow as it has room for.

    Returns how many were admitted. Commits once, then announces each.
    """
    flow = await engine.flow_repo.get_for_update(flow_id)
    if flow is None:
        return 0
    if flow.max_concurrent_runs is None:
        room = UNBOUNDED_ADMISSION_BATCH
    else:
        room = flow.max_concurrent_runs - await engine.run_repo.count_admitted(flow.id)
    if room <= 0:
        return 0
    admitted: list[tuple[WorkflowRunEntity, WorkflowRunWaitEntity | None]] = []
    for wait in await engine.wait_repo.oldest_queued(flow.id, limit=room):
        started = await _admit(engine, flow, wait)
        if started is not None:
            admitted.append(started)
    await engine.uow.commit()
    for run, pending_wait in admitted:
        await engine._announce(run)
        if pending_wait is not None:
            await engine._announce_human_wait(run, pending_wait)
    return len(admitted)


async def _admit(
    engine: "WorkflowEngine", flow: WorkflowEntity, queued: WorkflowRunWaitEntity
) -> tuple[WorkflowRunEntity, WorkflowRunWaitEntity | None] | None:
    """Advance one parked run from its entry node; its run and its next wait."""
    run = await engine.run_repo.get_for_update(queued.run_id)
    if run is None or run.status != WorkflowRunStatus.RUNNING:
        # Cancelled or failed while parked; the wait is all that is left.
        queued.cancel()
        await engine.wait_repo.update(queued)
        return None
    queued.complete()
    await engine.wait_repo.update(queued)
    try:
        ctx = await engine._require_action(
            requester_user_id=run.user_id,
            action=Permissions.WORKFLOW_EXECUTE,
            pod_id=run.pod_id,
            flow_id=flow.id,
        )
    except DomainError as exc:
        # Checked when it started, but it has waited since: the person who
        # started it may no longer be allowed to run this workflow.
        run.fail(f"Could not start after waiting for a place: {exc}")
        result = StepResult()
    else:
        result = await engine._stepper(ctx).advance(run, flow)
    run = await engine.run_repo.update(run)
    next_wait = await engine._persist_wait(run, result)
    engine._collect_terminal_event(run)
    return run, next_wait
