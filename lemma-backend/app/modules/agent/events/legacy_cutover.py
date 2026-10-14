"""The cutover's converters for what the old transport owed the agent module.

* ``agent.control`` (``agent.run.completed``) -- what a finished run leaves
  behind, written by ``services/run_followups.stage_run_followups`` as the run's
  own finalize writes it today: the queued-message follow-up, a sub-agent's
  result, a pod's answer, and the parent's SUBAGENT wait (the one fact
  subscriber that replaces this consumer's work), plus the title the old
  consumer also asked for. Each of those is idempotent on its own claim.
  ``agent.run.started`` and ``agent.run.stop_requested`` need nothing here:
  migration 0059 seeds a turn for every RUNNING or STOP_REQUESTED run.
* ``agent.notification_settled`` -- the ``NotificationSettled`` fact, for the
  asking conversation's next turn.
* ``streaq:generate_conversation_title`` -- the ``agent.title`` item.

Worker only.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.cutover.routes import LegacyPayload, legacy_converter
from app.core.infrastructure.work import JobContext, WorkWriter, facts, pod_namespace
from app.modules.agent.domain.value_objects import (
    AgentRunFinishResult,
    AgentRunStatus,
    ConversationStatus,
)
from app.modules.agent.events.turn_payloads import ConversationWork
from app.modules.agent.events.work_kinds import (
    AGENT_REPLIES_ARRIVED,
    AGENT_TITLE,
    AGENT_WAIT_RESOLVE_CHILD,
    title_key,
)
from app.modules.agent_surfaces.contracts.notification_facts import (
    NotificationSettled,
)

_FINISHED = frozenset(
    {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.STOPPED}
)


@legacy_converter("agent.control")
async def run_completed(ctx: JobContext, event: LegacyPayload, route: str) -> None:
    del route
    from app.modules.agent.infrastructure.repositories import ConversationRepository
    from app.modules.agent.services.run_followups import stage_run_followups

    conversation_id = UUID(str(event["conversation_id"]))
    run_id = UUID(str(event["agent_run_id"]))
    status = AgentRunStatus(str(event["status"]))
    if status not in _FINISHED:
        return
    async with ctx.complete_work() as uow:
        conversation = await ConversationRepository(uow).get_conversation(
            conversation_id
        )
        if conversation is None:
            return
        await stage_run_followups(
            uow,
            run_id=run_id,
            conversation_id=conversation_id,
            finish=AgentRunFinishResult(
                status=status,
                conversation_status=conversation.status
                or ConversationStatus(status.value),
                updated=True,
            ),
            fact_only={AGENT_WAIT_RESOLVE_CHILD},
        )
        if not conversation.title:
            await WorkWriter.of(uow).add(
                AGENT_TITLE,
                pod_namespace(conversation.pod_id),
                ConversationWork(conversation_id=conversation_id),
                dedup_key=title_key(conversation_id),
            )


@legacy_converter("agent.notification_settled")
async def notification_settled(
    ctx: JobContext, event: LegacyPayload, route: str
) -> None:
    del route
    fact = NotificationSettled(
        conversation_id=UUID(str(event["conversation_id"])),
        pod_id=UUID(str(event["pod_id"])),
    )
    async with ctx.complete_work() as uow:
        await facts.emit(uow, fact, only={AGENT_REPLIES_ARRIVED})


@legacy_converter("streaq:generate_conversation_title")
async def conversation_title(ctx: JobContext, event: LegacyPayload, route: str) -> None:
    del route
    from sqlalchemy import select

    from app.modules.agent.infrastructure.models import ConversationModel

    context = dict(dict(event.get("kwargs") or {}).get("context") or {})
    if not context.get("conversation_id"):
        return
    conversation_id = UUID(str(context["conversation_id"]))
    async with ctx.complete_work() as uow:
        pod_id = await uow.session.scalar(
            select(ConversationModel.pod_id).where(
                ConversationModel.id == conversation_id
            )
        )
        if pod_id is None:
            return
        await WorkWriter.of(uow).add(
            AGENT_TITLE,
            pod_namespace(pod_id),
            ConversationWork(conversation_id=conversation_id),
            dedup_key=title_key(conversation_id),
        )


__all__ = ["conversation_title", "notification_settled", "run_completed"]
