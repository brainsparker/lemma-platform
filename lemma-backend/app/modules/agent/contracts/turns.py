"""Starting an agent turn: the one call every run-start site makes.

A conversation turn starts in the transaction that writes its run and the
message it answers -- from a chat, a surface message, a resumed pause, a retry,
a workflow step. ``stage_agent_turn`` is that call. Today it hands the run to
the existing dispatch (an ``AgentRunStartedEvent`` collected on the unit of
work); once agent turns are work items it writes the ``agent.turn`` item
(keyed ``agent-run:<run_id>``, so a stop can cancel it) and the title item in
the same transaction. Callers do not change when that happens.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkClass
from app.modules.agent.contracts.run_facts import AgentRunOrigin
from app.modules.agent.domain.events import AgentRunStartedEvent
from app.modules.agent.services.run_dispatch import run_enqueue_suppressed

#: Origins whose turns nobody is watching live: they run in the automation
#: class, so a burst of them never queues behind (or ahead of) a person.
_AUTOMATION_ORIGINS: frozenset[AgentRunOrigin] = frozenset(
    {"schedule", "workflow", "trigger"}
)


@dataclass(frozen=True, slots=True)
class AgentTurnStart:
    """The run a turn executes, as the start site already holds it."""

    conversation_id: UUID
    run_id: UUID
    pod_id: UUID
    user_id: UUID
    agent_name: str | None = None
    origin: AgentRunOrigin = "chat"


def turn_class_for(origin: AgentRunOrigin) -> WorkClass:
    """The work class a turn started from ``origin`` runs in."""
    if origin in _AUTOMATION_ORIGINS:
        return WorkClass.AUTOMATION
    return WorkClass.INTERACTIVE


async def stage_agent_turn(
    uow: SqlAlchemyUnitOfWork,
    run: AgentTurnStart,
    *,
    cls: WorkClass | None = None,
) -> None:
    """Hand ``run`` to the worker when ``uow`` commits; nothing if it rolls back.

    ``cls`` overrides the class ``turn_class_for(run.origin)`` picks. Honours
    ``suppress_agent_run_enqueue`` (a caller running the turn inline).
    """
    del cls  # read once turns are work items; the event path has no classes
    if run_enqueue_suppressed():
        return
    uow.collect_events(
        [
            AgentRunStartedEvent(
                conversation_id=run.conversation_id,
                agent_run_id=run.run_id,
                user_id=run.user_id,
                pod_id=run.pod_id,
                agent_name=run.agent_name,
            )
        ]
    )


__all__ = ["AgentTurnStart", "stage_agent_turn", "turn_class_for"]
