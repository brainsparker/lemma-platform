"""What the engine writes and tells others once a run has moved.

Split out of ``engine.py`` at the 600-line ceiling ``scripts/check_architecture.py``
enforces. Every transition the engine makes ends the same way -- persist the
wait the stepper asked for, record a terminal event, push the committed run to
anyone streaming it, tell a form's assignee -- and this is that tail, kept as a
base of the engine rather than a collaborator for the reason ``wait_failure.py``
gives: it is engine bookkeeping that must be identical on every path.
"""

from __future__ import annotations

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.workflow.domain.events import WorkflowRunTerminalEvent
from app.modules.workflow.domain.ports import WorkflowNotificationPort
from app.modules.workflow.domain.run import (
    TERMINAL_STATUSES,
    WorkflowRunEntity,
    WorkflowRunStatus,
)
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
)
from app.modules.workflow.execution.stepper import StepResult
from app.modules.workflow.execution.wait_persistence import wait_entity_for
from app.modules.workflow.infrastructure.repositories import (
    SqlAlchemyWorkflowRepository,
    SqlAlchemyWorkflowRunWaitRepository,
)
from app.modules.workflow.infrastructure.run_channel import publish_run_state

logger = get_logger(__name__)


class RunPublication:
    uow: SqlAlchemyUnitOfWork
    flow_repo: SqlAlchemyWorkflowRepository
    wait_repo: SqlAlchemyWorkflowRunWaitRepository
    notification_adapter: WorkflowNotificationPort

    def _collect_terminal_event(self, run: WorkflowRunEntity) -> None:
        if run.status not in TERMINAL_STATUSES:
            return
        self.uow.collect_events([WorkflowRunTerminalEvent.from_run(run)])

    async def _announce(self, run: WorkflowRunEntity) -> None:
        """Push the committed run to anyone streaming it.

        Called only after `uow.commit()` — a subscriber must never observe state
        the database has not accepted. Imported lazily to keep the API schema
        module out of the engine's import cycle.
        """
        from app.modules.workflow.api.schemas import run_response_from_domain

        # Everything here is best effort and must stay that way: the run is
        # already committed, so a failure to *tell anyone* cannot be allowed to
        # turn a successful advance into a raised exception. Clients still poll.
        try:
            wait = await self.wait_repo.get_active_for_run(run.id)
            title = await self.flow_repo.run_title_of(run.flow_id)
            payload = run_response_from_domain(run, wait, title).model_dump(mode="json")
            # The wait read above needs a connection; the Redis publish does
            # not, and this runs after every node advance.
            async with connection_released(getattr(self.wait_repo, "session", None)):
                await publish_run_state(
                    run.id,
                    payload,
                    terminal=run.status in TERMINAL_STATUSES,
                )
        except Exception:
            logger.debug("workflow.run.announce_failed", exc_info=True)

    async def _persist_wait(
        self, run: WorkflowRunEntity, result: StepResult
    ) -> WorkflowRunWaitEntity | None:
        """Write the wait row. Telling the assignee happens after the commit.

        It used to happen here, which put a Slack/Teams/WhatsApp/email send
        inside the run transaction and under the run's row lock — for the
        seconds a platform API can take, with a pooled connection held. The
        cheap Redis announce next door already had the right sequencing, and
        says why in its own docstring; the expensive one had the opposite.
        """
        if result.wait is None or run.status not in (
            WorkflowRunStatus.WAITING,
            WorkflowRunStatus.RUNNING,
        ):
            return None
        return await self.wait_repo.create(wait_entity_for(run, result.wait))

    async def _announce_human_wait(
        self, run: WorkflowRunEntity, wait: WorkflowRunWaitEntity
    ) -> None:
        """Tell the assignee a form is waiting on them.

        Until this existed the wait was a pure pull queue: the row was written,
        the "waiting for you" list rendered it, and nobody was ever told. A
        workflow could sit for days on someone who had no idea it existed.

        Unassigned waits are skipped on purpose — there is no one person to tell,
        and broadcasting to the pod would make every form everyone's problem.
        """
        if (
            wait.wait_type is not WorkflowRunWaitType.HUMAN
            or wait.assigned_pod_member_id is None
        ):
            return
        flow = await self.flow_repo.get(run.flow_id)
        await self.notification_adapter.notify_form_assignee(
            pod_id=run.pod_id,
            run_id=run.id,
            flow_id=run.flow_id,
            node_id=wait.node_id,
            assigned_pod_member_id=wait.assigned_pod_member_id,
            flow_name=getattr(flow, "name", None),
            schema=wait.payload.get("input_schema"),
            actor_user_id=run.user_id,
        )
