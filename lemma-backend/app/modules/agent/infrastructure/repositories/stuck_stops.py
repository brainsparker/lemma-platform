"""Finishing stops that no worker ever acted on."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.modules.agent.domain.events import AgentRunCompletedEvent
from app.modules.agent.domain.value_objects import AgentRunStatus

if TYPE_CHECKING:
    from app.modules.agent.domain.conversation_ports import StuckStopSweep


async def settle_stuck_stops(
    repository: StuckStopSweep, *, cutoff_seconds: int
) -> list[tuple[UUID, UUID, AgentRunStatus]]:
    """Finish stops that no worker ever acted on, as STOPPED.

    A live worker acts on a stop within a second, so one still pending after the
    cutoff means none will. STOP_REQUESTED is an active status and holds the
    conversation's one active run slot, so until it settles a new message
    attaches to the dying run and starts nothing, and Retry refuses. Before this
    the only thing that freed the conversation was the orphan sweep, an hour
    after the run started.

    STOPPED rather than FAILED: the user asked for this one to end, and it did.
    Returns ``(conversation_id, run_id, status)`` for each run it finished.
    """
    settled: list[tuple[UUID, UUID, AgentRunStatus]] = []
    for run in await repository.list_runs_stuck_stopping(cutoff_seconds=cutoff_seconds):
        result = await repository.finish_agent_run(
            agent_run_id=run.id,
            status=AgentRunStatus.STOPPED,
        )
        if result is None or not result.updated:
            continue
        repository.collect_events(
            [
                AgentRunCompletedEvent(
                    conversation_id=run.conversation_id,
                    agent_run_id=run.id,
                    status=result.status,
                    data={},
                )
            ]
        )
        settled.append((run.conversation_id, run.id, result.status))
    return settled
