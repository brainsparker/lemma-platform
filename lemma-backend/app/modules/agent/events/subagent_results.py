"""Carrying a sub-agent's result to its parent the moment a run finishes.

``SubagentResultDelivery.settle`` decides what is owed; this connects it to
``AgentRunCompletedEvent``, which arrives inside an idempotency inbox, and the
per-run claim makes a redelivered event deliver nothing.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.services.subagent_result_delivery import (
    SubagentResultDelivery,
)

logger = get_logger(__name__)


async def deliver_subagent_result(
    event: AgentRunCompletedEvent, *, uow_factory: UnitOfWorkFactory
) -> None:
    """Deliver the results this run's completion owes, absorbing what a retry
    can't fix.

    Absorbed for the reasons ``pod_ask_answers`` absorbs them: titling and
    follow-ups ride the same event, and a ``DomainError`` -- the parent's owner
    over their usage limit, say -- would meet the same refusal on a retry. The
    claim rolls back with the failed delivery, so the result stays owed and the
    parent's next finished run collects it.
    """
    try:
        await SubagentResultDelivery(uow_factory).settle(
            conversation_id=event.conversation_id
        )
    except SQLAlchemyError, DomainError:
        logger.warning(
            "agent.subagent_result.delivery_failed.degraded",
            conversation_id=str(event.conversation_id),
            agent_run_id=str(event.agent_run_id),
            exc_info=True,
        )
