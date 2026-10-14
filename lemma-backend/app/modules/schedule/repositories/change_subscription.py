"""Keeping a DATASTORE trigger's change-log subscriber in step with its row.

Called by every write that can change what a trigger watches -- create, an
edit of its config or active flag, a pause, a delete -- in that write's
transaction. The ``changes.sync_subscribers`` item it writes reads the
schedule back after commit and creates, updates or drops the subscriber, so
the change log never needs to be told *what* changed, only that something may
have.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.contracts.changes import (
    request_subscriber_sync,
    subscriber_key,
)
from app.modules.schedule.domain.schedule import ScheduleType

PREFIX = "schedule"


class TriggerRow(Protocol):
    """A schedule row, or a ``RETURNING pod_id, schedule_type`` row."""

    @property
    def pod_id(self) -> UUID | None: ...

    @property
    def schedule_type(self) -> ScheduleType | str: ...


#: An edit of anything else leaves what a trigger watches as it was.
_WATCHED_FIELDS = frozenset({"config", "is_active"})


async def sync_trigger_subscription(
    uow: SqlAlchemyUnitOfWork,
    schedule_id: UUID,
    row: TriggerRow,
    changed: Collection[str] | None = None,
) -> None:
    """``changed``: the fields an edit wrote; ``None`` for a create, pause or delete."""
    if changed is not None and not _WATCHED_FIELDS.intersection(changed):
        return
    if row.pod_id is None or ScheduleType(row.schedule_type) != ScheduleType.DATASTORE:
        return
    await request_subscriber_sync(uow, row.pod_id, subscriber_key(PREFIX, schedule_id))


__all__ = ["PREFIX", "sync_trigger_subscription"]
