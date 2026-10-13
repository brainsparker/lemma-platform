"""The backstop for messages no run ever read.

A message sent mid-run joins the run already in flight, which loaded its history
before that message existed. `PendingUserMessagesCapability` is what normally
carries it the rest of the way — it claims the message and steers it into that
same run, so one reply covers everything and nothing reaches here.

An Agent Host run is steered too, when its harness advertises ACP steering (see
`harnesses/agent_host/steering.py`). This exists for what neither can cover: a
harness that cannot steer, a steer that did not land before the turn ended, and
a run that died before draining its queue. Each leaves a person waiting on an
answer that will otherwise never come.

Hung off `agent.run.completed` rather than off the end of the runner because
every way a run can end publishes that event: a normal finish, a failure, and
the cron that reconciles runs whose worker died. A run stopped on purpose
answers only what was said after Stop, and a run that ended on a question
resumes if the question was answered before it finished ending.

Lives in the events layer because it is composition — it builds a service out of
a worker's unit of work — and because `handlers` is against the file-size
ratchet.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.usage.contracts.execution import build_usage_service
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.scope import context_scope
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.services.realtime import (
    message_payload,
    publish_conversation_event,
)
from app.modules.agent.services.serialization import message_to_payload

logger = get_logger(__name__)


async def start_followup_run_for_queued_messages(
    event: AgentRunCompletedEvent,
    *,
    uow_factory: UnitOfWorkFactory,
) -> UUID | None:
    """Start a turn for anything queued behind the run that just ended.

    Returns the new run's id, or None when there was nothing queued — which is
    the overwhelmingly common case, and costs one indexed count to establish.

    Never raises: a conversation that cannot take another turn right now (usage
    exhausted, a run already active again) must not poison the completion event,
    which is redelivered until it is acked.
    """
    # A stopped run is not skipped here: what was said *after* Stop is still
    # owed an answer, and `start_queued_followup` keeps to exactly that.
    try:
        async with uow_factory() as uow:
            conversation_repository = ConversationRepository(uow)
            conversation = await conversation_repository.get_conversation(
                event.conversation_id
            )
            if conversation is None:
                return None
            # The run acts for the conversation's owner, as every run of it
            # does — matching `reconcile_agent_approval_now`, and for the same
            # reason: this worker job has no ambient context of its own.
            auth_ctx = await create_authorization_data_service(uow).build_user_context(
                user_id=conversation.user_id,
                pod_id=conversation.pod_id,
            )
            async with context_scope(auth_ctx):
                service = ConversationService(
                    uow=uow,
                    conversation_repository=conversation_repository,
                    agent_repository=AgentRepository(uow),
                    usage_service=build_usage_service(uow),
                )
                started = await service.turns.start_queued_followup(
                    conversation=conversation,
                    completed_run_id=event.agent_run_id,
                )
        if started is None:
            return None
        followup_run_id, owed_frames = started
        # Outside the unit of work, as the stop path publishes: a session holds
        # a pooled connection until it closes, and these are Redis round trips.
        for message in owed_frames:
            await publish_conversation_event(
                event.conversation_id,
                message_payload(message.agent_run_id, message_to_payload(message)),
            )
        return followup_run_id
    except Exception:
        # Degraded rather than error: the messages are still durably in the
        # conversation and the person's next one picks them up. What is lost is
        # the automatic turn, and that is worth a line naming the conversation.
        logger.warning(
            "agent.queued_followup.start_failed.degraded",
            conversation_id=event.conversation_id,
            agent_run_id=event.agent_run_id,
            exc_info=True,
        )
        return None
