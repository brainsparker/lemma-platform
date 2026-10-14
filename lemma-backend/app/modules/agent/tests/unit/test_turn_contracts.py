"""The contracts later workstreams build agent turns on: turn start, its class, the run fact."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

from app.core.domain.events import DomainEvent
from app.core.infrastructure.work import WorkClass
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.contracts.turns import (
    AgentTurnStart,
    stage_agent_turn,
    turn_class_for,
)
from app.modules.agent.domain.events import AgentRunStartedEvent
from app.modules.agent.events import work_kinds
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue


class _Uow:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    def collect_events(self, events: Sequence[DomainEvent]) -> None:
        self.events.extend(events)


def _start() -> AgentTurnStart:
    return AgentTurnStart(
        conversation_id=uuid4(), run_id=uuid4(), pod_id=uuid4(), user_id=uuid4()
    )


async def test_stage_agent_turn_hands_the_run_to_the_existing_dispatch() -> None:
    uow = _Uow()
    run = _start()

    await stage_agent_turn(uow, run)  # type: ignore[arg-type]

    [event] = uow.events
    assert isinstance(event, AgentRunStartedEvent)
    assert (event.agent_run_id, event.conversation_id) == (
        run.run_id,
        run.conversation_id,
    )


async def test_stage_agent_turn_honours_an_inline_caller() -> None:
    uow = _Uow()
    with suppress_agent_run_enqueue():
        await stage_agent_turn(uow, _start())  # type: ignore[arg-type]
    assert uow.events == []


def test_automation_origins_run_in_the_automation_class() -> None:
    assert turn_class_for("schedule") is WorkClass.AUTOMATION
    assert turn_class_for("workflow") is WorkClass.AUTOMATION
    assert turn_class_for("trigger") is WorkClass.AUTOMATION
    assert turn_class_for("chat") is WorkClass.INTERACTIVE
    assert turn_class_for("surface") is WorkClass.INTERACTIVE


def test_the_run_fact_runs_its_subscribers_in_the_pod() -> None:
    pod_id = uuid4()
    fact = AgentRunFinished(
        run_id=uuid4(),
        conversation_id=uuid4(),
        pod_id=pod_id,
        status="COMPLETED",
        conversation_status="COMPLETED",
        origin="chat",
    )
    assert fact.work_namespace() == f"pod:{pod_id}"


def test_the_turn_key_is_the_one_stop_cancels_by() -> None:
    run_id = uuid4()
    assert work_kinds.turn_key(run_id) == f"agent-run:{run_id}"
