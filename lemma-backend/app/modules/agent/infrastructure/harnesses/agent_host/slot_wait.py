"""A run queued behind others on a computer whose run slots are all taken.

The computer is there and will take the run when one of its own ends, so the
run is early, not lost. It used to fail at its wait deadline -- "No Agent Host
received the run" -- because two other runs on the same machine went long,
after minutes of showing nothing at all. While the computer keeps heartbeating
with no slot free, ``hold_for_busy_host`` pushes the run's lease out, and the
conversation is told what it is waiting on: once, and again only if that
changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.value_objects import AgentEvent
from app.modules.agent.infrastructure.agent_host.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host.recovery import BusyHost
from app.modules.agent.infrastructure.harnesses.agent_host.event_text import (
    waiting_for_slot_event,
)


@dataclass(slots=True)
class SlotWait:
    """One queued run's wait on a full computer, between lease checks."""

    wait_seconds: int
    #: The busy computer the conversation was last told about.
    waiting_on: BusyHost | None = None

    async def check(
        self, uow_factory: UnitOfWorkFactory, *, agent_run_id: UUID
    ) -> tuple[bool, AgentEvent | None]:
        """Whether the run was held, and the notice to show if there is news."""
        async with uow_factory() as uow:
            busy = await AgentHostDispatchRepository(uow).hold_for_busy_host(
                run_id=agent_run_id, wait_seconds=self.wait_seconds
            )
            await uow.commit()
        if busy is None:
            self.waiting_on = None
            return False, None
        if busy == self.waiting_on:
            return True, None
        self.waiting_on = busy
        return True, waiting_for_slot_event(
            agent_run_id, host_name=busy.name, active_runs=busy.active_runs
        )
