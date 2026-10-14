"""In-memory stand-ins for the function module's ports (TST-03)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.modules.function.domain.ports import PodRunSlotsPort


class InMemoryPodRunSlots(PodRunSlotsPort):
    """Never full unless told to be; records what was taken and given back."""

    def __init__(self, *, limit: int = 1_000) -> None:
        self.limit = limit
        self.held: dict[UUID, set[UUID]] = {}
        self.released: list[UUID] = []

    async def acquire(
        self,
        pod_id: UUID,
        run_id: UUID,
        *,
        hold_until: datetime,
        wait_until: datetime,
    ) -> bool:
        del hold_until, wait_until
        runs = self.held.setdefault(pod_id, set())
        if run_id not in runs and len(runs) >= self.limit:
            return False
        runs.add(run_id)
        return True

    async def extend(self, pod_id: UUID, run_id: UUID, *, hold_until: datetime) -> None:
        del pod_id, run_id, hold_until

    async def release(self, pod_id: UUID, run_id: UUID) -> None:
        self.held.get(pod_id, set()).discard(run_id)
        self.released.append(run_id)
