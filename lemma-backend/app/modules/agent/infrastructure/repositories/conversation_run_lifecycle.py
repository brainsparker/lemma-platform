"""Ending a run, and moving its conversation's status with it.

Split from `ConversationRepository` the way the run and approval queries were:
these are the writes that end a run, which several parties race to make -- the
worker finishing it, a person pressing Stop, the sweep that settles a run whose
worker died -- and they are easier to hold to one rule when they sit together.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.modules.agent.domain.value_objects import (
    TERMINAL_AGENT_RUN_STATUSES,
    AgentRunFinishResult,
    AgentRunStatus,
    ConversationStatus,
    JsonValue,
)
from app.modules.agent.infrastructure.models import AgentRunModel, ConversationModel
from app.modules.agent.infrastructure.repositories.conversation_activity import (
    touch_activity as _touch_activity,
)
from app.modules.agent.infrastructure.repositories.conversation_status_repair import (
    reconcile_conversation_to_terminal,
)


class ConversationRunLifecycleMixin:
    """Run terminal writes mixed into ``ConversationRepository``."""

    async def finish_agent_run(
        self,
        *,
        agent_run_id: UUID,
        status: AgentRunStatus,
        conversation_status: ConversationStatus | None = None,
        error: str | None = None,
        error_code: str | None = None,
        error_reason: str | None = None,
        output_data: JsonValue | None = None,
    ) -> AgentRunFinishResult | None:
        result = await self.session.execute(
            select(AgentRunModel).where(AgentRunModel.id == agent_run_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None

        current_status = AgentRunStatus(model.status)
        resolved_conversation_status = conversation_status or ConversationStatus(
            current_status.value
        )
        if current_status in TERMINAL_AGENT_RUN_STATUSES:
            # Nothing to end — but this used to report a `conversation_status`
            # it had only *inferred* from the run row and never written, so a
            # conversation out of step stayed that way. Nothing else recovers
            # one: the orphan sweep keys on `agent_runs.status`, so a terminal
            # run is invisible to it. This is the only place seeing both rows.
            repaired = await reconcile_conversation_to_terminal(
                self.session,
                conversation_id=model.conversation_id,
                status=resolved_conversation_status,
            )
            return AgentRunFinishResult(
                status=current_status,
                conversation_status=resolved_conversation_status,
                updated=False,
                conversation_repaired=repaired,
            )

        next_status = status
        if (
            current_status == AgentRunStatus.STOP_REQUESTED
            and status in TERMINAL_AGENT_RUN_STATUSES
        ):
            # Stopped, not failed: it must not inherit a failure's message.
            next_status, error = AgentRunStatus.STOPPED, None

        model.status = next_status.value
        model.error = error
        metadata = dict(model.run_metadata or {})
        metadata.pop("failure", None)
        if next_status == AgentRunStatus.FAILED and error_code:
            metadata["failure"] = {"code": error_code, "reason": error_reason}
        model.run_metadata = metadata
        if output_data is not None:
            model.output_data = output_data
        resolved_conversation_status = conversation_status or ConversationStatus(
            next_status.value
        )
        await self._update_conversation_status(
            conversation_id=model.conversation_id,
            status=resolved_conversation_status,
            output_data=output_data,
            touch_activity=True,
        )
        if next_status in TERMINAL_AGENT_RUN_STATUSES:
            model.finished_at = datetime.now(timezone.utc)
        await self.session.flush()
        return AgentRunFinishResult(
            status=next_status,
            conversation_status=resolved_conversation_status,
            updated=True,
        )

    async def set_conversation_status(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus,
    ) -> None:
        """Move a conversation's status with no run to finish.

        ``finish_agent_run`` is the usual path, but a suspended conversation has
        no active run — the run ended when the tool paused it — so cancelling its
        wait has to set the status directly.
        """
        await self._update_conversation_status(
            conversation_id=conversation_id,
            status=status,
        )

    async def _update_conversation_status(
        self,
        *,
        conversation_id: UUID,
        status: ConversationStatus,
        output_data: JsonValue | None = None,
        touch_activity: bool = False,
    ) -> None:
        conversation = await self.session.get(ConversationModel, conversation_id)
        if conversation is None:
            return
        conversation.status = status.value
        # A run starting (every wake starts one) or ending is activity; a
        # cancelled wait or a repaired status is not. Same UPDATE either way.
        if touch_activity:
            _touch_activity(conversation)
        if output_data is not None or status == ConversationStatus.RUNNING:
            conversation.output_data = output_data
