"""``surface.pause_lines``: a run ended, so its questions' lines catch up.

Subscribed to ``AgentRunFinished`` for every run: one that paused posts its
new questions as lines waiting on the person; any run's end -- the resume
after an answer, a stop, a failure -- closes the lines of questions no longer
waiting. FAST: main-database statements only, committed with the item.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_PAUSE_LINES,
    pause_lines_key,
)
from app.modules.agent_surfaces.services.pause_lines import sync_pause_lines


class PauseLinesPayload(BaseModel):
    """The fields of ``AgentRunFinished`` this needs; the rest are ignored."""

    run_id: UUID
    conversation_id: UUID


def concerns_a_person(fact: AgentRunFinished) -> bool:
    """A run of a conversation somebody could have been asked something in:
    a member's, which a person -- not a pod, not a parent agent -- answers."""
    return fact.user_id is not None and fact.parent_kind != "agent"


async def sync_lines(ctx: FastContext, payload: PauseLinesPayload) -> None:
    await sync_pause_lines(ctx.uow, payload.conversation_id)


def register_pause_lines_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACE_PAUSE_LINES,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=PauseLinesPayload,
    )(sync_lines)


register_pause_lines_work(default_registry)
facts.subscribe(
    AgentRunFinished,
    kind=SURFACE_PAUSE_LINES,
    cls=WorkClass.EVENTS,
    when=concerns_a_person,
    dedup=lambda fact: pause_lines_key(fact.run_id),
)
