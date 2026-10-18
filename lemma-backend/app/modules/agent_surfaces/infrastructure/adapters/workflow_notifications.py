"""Telling a person a workflow is waiting on them.

A ``FORM`` node assigned to a pod member puts a line in that member's own
channel asking for it: in their inbox beside every other question put to
them, answered in place or in the workflow's own screen. The engine validates
whatever is submitted and closes the line through ``close_form_notification``;
a cancelled run cancels its lines.

`agent_surfaces`' half of the bridge: published as a factory from
`contracts/workflow_notifications.py`, so `workflow` names the port and never
this class. Each method catches only what must not fail a run -- this module's
refusals and infrastructure blips -- and logs it with ``exc_info``; anything
else is a bug and propagates.
"""

from __future__ import annotations

from uuid import UUID

from httpx import HTTPError
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.transient_errors import raise_if_database_unavailable
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.core.authorization.delegation import effective_agent_id
from app.modules.agent.contracts.agents import agent_name_for_id
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.channels.contracts import (
    InteractionKind,
    InteractionState,
    NewQuestion,
    Principal,
    Sender,
    direct_channel,
    post,
)
from app.modules.channels.contracts.questions import (
    AnswerRefused,
    Answering,
    answers_for,
    close_question,
    close_questions_under,
    question_for,
)
from app.modules.workflow.contracts.forms import submit_workflow_form
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError

logger = get_logger(__name__)

#: What a form line must survive rather than fail a workflow run over. See the
#: module docstring: not `Exception`, because the platform failures that reason
#: names are already handled where the line is carried out.
_SURVIVABLE = (AgentSurfaceError, SQLAlchemyError, OSError, HTTPError)
#: What a form's question ``ref`` starts with.
FORM_REF = "wf"


def describe_fields(schema: dict[str, object] | None) -> str:
    """A one-line summary of what the form wants, for the message body.

    Reads the resolved schema the form executor already stored on the wait, so
    the recipient is told what is being asked rather than just that something is.
    """
    if not isinstance(schema, dict):
        return ""
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return ""
    labels = []
    for name, spec in properties.items():
        title = None
        if isinstance(spec, dict):
            title = spec.get("title") or spec.get("description")
        labels.append(str(title or name))
    return ", ".join(labels[:8])


def form_ref(run_id: UUID, node_id: str) -> str:
    """The ref of the line asking for a run's form at one node."""
    return f"{FORM_REF}:{run_id}:{node_id}"


class WorkflowNotificationAdapter:
    """Posts, and closes, the line that asks somebody to fill a workflow's form.

    The line is in the assignee's own channel, waiting on them: in their inbox
    beside every other question put to them, answered in place
    (``answer_form``) or in the workflow's own screen -- either way the engine
    validates it, and closes the line here.
    """

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    async def notify_form_assignee(
        self,
        *,
        pod_id: UUID,
        run_id: UUID,
        flow_id: UUID,
        node_id: str,
        assigned_pod_member_id: UUID,
        flow_name: str | None,
        schema: dict[str, object] | None,
        actor_user_id: UUID | None,
    ) -> None:
        try:
            await self._ask(
                pod_id=pod_id,
                run_id=run_id,
                flow_id=flow_id,
                node_id=node_id,
                assigned_pod_member_id=assigned_pod_member_id,
                flow_name=flow_name,
                schema=schema,
                actor_user_id=actor_user_id,
            )
        except _SURVIVABLE as exc:
            raise_if_database_unavailable(exc)
            logger.warning(
                "workflow.notifications.form_notify_failed.degraded",
                run_id=str(run_id),
                node_id=node_id,
                error=str(exc),
                exc_info=True,
            )

    async def _ask(
        self,
        *,
        pod_id: UUID,
        run_id: UUID,
        flow_id: UUID,
        node_id: str,
        assigned_pod_member_id: UUID,
        flow_name: str | None,
        schema: dict[str, object] | None,
        actor_user_id: UUID | None,
    ) -> None:
        recipient_user_id = await SqlAlchemySurfaceRoutingResolutionAdapter(
            self._uow
        ).resolve_pod_recipient(pod_id=pod_id, reference=str(assigned_pod_member_id))
        if recipient_user_id is None:
            logger.warning(
                "workflow.notifications.assignee_unresolved.degraded",
                run_id=str(run_id),
                node_id=node_id,
            )
            return
        ref = form_ref(run_id, node_id)
        # One per (run, node): a retried job, or a run re-entering the node,
        # must not stack a second question on the person.
        asked = await question_for(self._uow, ref)
        if asked is not None and asked.interaction and asked.interaction.is_open:
            return
        fields = describe_fields(schema)
        title = f"{flow_name or 'A workflow'} needs your input"
        channel = await direct_channel(
            self._uow, pod_id=pod_id, user_id=recipient_user_id
        )
        agent_id = effective_agent_id(None, pod_id=pod_id)
        await post(
            self._uow,
            channel_id=channel.id,
            sender=Sender.agent(
                agent_id,
                on_behalf_of=Principal.member(actor_user_id or recipient_user_id),
                display_name=await agent_name_for_id(self._uow.session, agent_id),
            ),
            text=f"{title}." + (f" It asks for: {fields}." if fields else ""),
            question=NewQuestion(
                kind=InteractionKind.FORM,
                question={
                    "ref": ref,
                    "tool": "workflow_form",
                    "prompt": title,
                    "run_id": str(run_id),
                    "flow_id": str(flow_id),
                    "node_id": node_id,
                    # The concrete, template-resolved schema on the wait, so
                    # the form renders where it is asked.
                    "schema": schema,
                },
                awaits=Principal.member(recipient_user_id),
            ),
        )

    async def close_form_notification(
        self,
        *,
        pod_id: UUID,
        run_id: UUID,
        node_id: str,
        summary: str,
        data: dict[str, object] | None = None,
    ) -> None:
        """The form was submitted -- validated against its schema, by its
        assignee -- so the line asking for it is answered with what was sent."""
        del pod_id, summary
        try:
            await close_question(
                self._uow,
                ref=form_ref(run_id, node_id),
                state=InteractionState.ANSWERED,
                answer=dict(data or {}),
            )
        except _SURVIVABLE as exc:
            logger.warning(
                "workflow.notifications.form_close_failed.degraded",
                run_id=str(run_id),
                node_id=node_id,
                error=str(exc),
                exc_info=True,
            )

    async def cancel_for_run(self, *, run_id: UUID) -> None:
        """A cancelled run leaves nothing waiting on anybody."""
        try:
            await close_questions_under(
                self._uow,
                prefix=f"{FORM_REF}:{run_id}:",
                state=InteractionState.CANCELLED,
            )
        except _SURVIVABLE as exc:
            logger.warning(
                "workflow.notifications.cancel_failed.degraded",
                run_id=str(run_id),
                error=str(exc),
                exc_info=True,
            )


@answers_for(FORM_REF)
async def answer_form(answering: Answering) -> None:
    """A form answered on its line: submitted through the engine, which checks
    it is theirs and validates it, then closes the line."""
    question = (
        answering.message.interaction.question if answering.message.interaction else {}
    )
    try:
        run_id = UUID(str(question.get("run_id")))
    except ValueError:
        raise AnswerRefused(
            "This form cannot be answered here", status_code=404
        ) from None
    submitted, said = await submit_workflow_form(
        run_id=run_id,
        node_id=str(question.get("node_id") or ""),
        inputs=dict(answering.answer),
        requester_user_id=answering.user_id,
    )
    if not submitted:
        raise AnswerRefused(said, status_code=422)


__all__ = [
    "FORM_REF",
    "WorkflowNotificationAdapter",
    "answer_form",
    "describe_fields",
    "form_ref",
]
