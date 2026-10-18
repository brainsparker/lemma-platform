"""What a conversation is waiting on people for, for whoever shows it to them.

Every ``ask_user`` and ``request_approval`` nobody has decided yet, oldest
first, and the decisions taken on ones that were -- so a place that shows
these questions (a channel's line asking it) can ask what is still open, and
say what was answered elsewhere: in the conversation, on a platform's card,
in a reply typed on the platform.

A submodule rather than ``contracts/__init__``: only what shows questions
outside the conversation reads it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.conversations_for_surfaces import PendingInteraction
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.domain.pausing_tools import USER_PAUSING_TOOL_NAMES
from app.modules.agent.infrastructure.models.approval import AgentApprovalDecisionModel
from app.modules.agent.infrastructure.repositories.conversation_repository import (
    ConversationRepository,
)
from app.modules.agent.services.service_factories import build_conversation_service

#: The most questions one conversation is shown waiting on at once.
MAX_PAUSES = 20


@dataclass(frozen=True, slots=True)
class PauseDecision:
    """How a question was decided: ``APPROVED``, ``DENIED``, or an answer."""

    decision: str
    response: dict[str, object] | None
    resolved_by_user_id: UUID | None


async def undecided_pauses_in(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> list[PendingInteraction]:
    """Every question this conversation waits on a person for, oldest first."""
    calls = await ConversationRepository(uow).pausing_calls_awaiting_a_decision(
        conversation_id=conversation_id,
        pausing_tool_names=USER_PAUSING_TOOL_NAMES,
        limit=MAX_PAUSES,
    )
    return [
        PendingInteraction(
            tool_call_id=call.tool_call_id,
            kind=call.tool_name or "",
            tool_args=dict(call.tool_args) if isinstance(call.tool_args, dict) else {},
            agent_run_id=call.agent_run_id,
        )
        for call in calls
        if call.tool_call_id
    ]


async def pause_decisions(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, tool_call_ids: Sequence[str]
) -> dict[str, PauseDecision]:
    """The decision taken on each of these questions, where one was."""
    if not tool_call_ids:
        return {}
    rows = await uow.session.execute(
        select(
            AgentApprovalDecisionModel.approval_id,
            AgentApprovalDecisionModel.decision,
            AgentApprovalDecisionModel.response,
            AgentApprovalDecisionModel.resolved_by_user_id,
        ).where(
            AgentApprovalDecisionModel.conversation_id == conversation_id,
            AgentApprovalDecisionModel.approval_id.in_(list(tool_call_ids)),
        )
    )
    return {
        approval_id: PauseDecision(
            decision=decision,
            response=dict(response) if isinstance(response, dict) else None,
            resolved_by_user_id=resolved_by,
        )
        for approval_id, decision, response, resolved_by in rows.tuples()
    }


async def answer_pause(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    tool_call_id: str,
    user_id: UUID,
    pod_id: UUID,
    decision: str,
    response: dict[str, object] | None,
) -> str:
    """Answer one of a conversation's questions as ``user_id``, the way the
    conversation's own card does: authorized as them (the ambient context is
    theirs), the run resumed, and the cards on platforms retired once the
    answer commits. The resolution's status: ``"queued"`` when an approved
    action runs in a worker.

    Raises ``ValueError`` for a decision that is not one.
    """
    resolution = await build_conversation_service(uow).resolve_user_approval(
        conversation_id=conversation_id,
        approval_id=tool_call_id,
        user_id=user_id,
        pod_id=pod_id,
        decision=AgentRunApprovalDecision(decision),
        response=response,
    )
    return resolution.status


__all__ = [
    "PauseDecision",
    "answer_pause",
    "pause_decisions",
    "undecided_pauses_in",
]
