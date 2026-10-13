"""The worker half of a durable approval decision.

Split out of `events/handlers.py`, which sat at the architecture ratchet's size
ceiling. Registered on import: `handlers` imports it, so the task is on the
worker wherever it was before.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.modules.usage.contracts.execution import build_usage_service
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.scope import context_scope
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_runtime import (
    AppWorkerContext,
    streaq_task,
    streaq_worker,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.conversation_service import ConversationService


@streaq_task(name="reconcile_agent_approval")
async def reconcile_agent_approval(
    context: dict[str, str | None],
) -> None:
    """Execute a durable approval decision outside the HTTP request deadline."""
    worker_ctx: AppWorkerContext = streaq_worker.context
    await reconcile_reporting_failure(context, uow_factory=worker_ctx.uow_factory)


async def reconcile_reporting_failure(
    context: dict[str, str | None],
    *,
    uow_factory: UnitOfWorkFactory,
) -> None:
    """Reconcile, and tell the conversation's platform if that fails.

    A chat tap on Approve is answered "running it now" before this job runs,
    so a failure here is a broken promise the person would otherwise never
    hear about. Not on a cancellation: a worker shutting down hands the job to
    the next one, and nothing has failed yet. The failure still propagates, so
    the job is recorded as failed.
    """
    outcome = "failed"
    try:
        await reconcile_agent_approval_now(context, uow_factory=uow_factory)
        outcome = "reconciled"
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    finally:
        if outcome == "failed":
            # Lazily, as `handlers` reaches the surfaces egress contract: it is
            # only needed on this path, and importing it loads the platforms.
            from app.modules.agent_surfaces.contracts.egress import (
                report_approved_action_failed,
            )

            await report_approved_action_failed(
                uow_factory=uow_factory,
                conversation_id=UUID(str(context["conversation_id"])),
            )


async def reconcile_agent_approval_now(
    context: dict[str, str | None],
    *,
    uow_factory: UnitOfWorkFactory,
) -> None:
    """Reconcile one already-recorded decision; split out for focused tests.

    Both lookups can legitimately come back empty — the conversation was deleted,
    or this job outran the transaction that recorded the decision — and neither
    is an error worth retrying, so both simply return.
    """
    conversation_id = UUID(str(context["conversation_id"]))
    approval_id = str(context["approval_id"])
    pod_id = UUID(str(context["pod_id"]))

    async with uow_factory() as uow:
        conversation_repository = ConversationRepository(uow)
        conversation = await conversation_repository.get_conversation(conversation_id)
        if conversation is None:
            return
        recorded = await conversation_repository.get_approval_decision(
            conversation_id=conversation_id,
            approval_id=approval_id,
        )
        if recorded is None:
            return
        decision, response = recorded
        service = ConversationService(
            uow=uow,
            conversation_repository=conversation_repository,
            agent_repository=AgentRepository(uow),
            authorization_service=create_authorization_data_service(uow),
            usage_service=build_usage_service(uow),
        )
        # An approved request_approval runs its wrapped tool with a user's
        # authority, which needs that user's authorization context bound — the
        # request that recorded this decision had one, this worker job does not.
        # Both halves must name the SAME user or the tool runs with one
        # principal's ambient permissions under another's identity. It is the
        # conversation owner, per resolve_user_approval_internal's contract and
        # matching the surface approval path: the agent acts for the owner, and
        # whoever clicked approve is deciding, not lending their authority.
        # ``user_id`` from the job context is the resolver and is only recorded
        # as such, which already happened before this job was queued.
        auth_ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )
        async with context_scope(auth_ctx):
            await service.resolve_user_approval_internal(
                conversation=conversation,
                approval_id=approval_id,
                user_id=conversation.user_id,
                pod_id=pod_id,
                decision=decision,
                response=response,
            )


__all__ = [
    "reconcile_agent_approval",
    "reconcile_agent_approval_now",
    "reconcile_reporting_failure",
]
