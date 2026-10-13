"""One page of record changes, delivered to one DATASTORE trigger.

The shared half of DATASTORE delivery: whatever reads a pod's changes (the
change log's ``changes.deliver``) hands each trigger a page of changes already
narrowed to its table and past its cursor, and this decides, in the caller's
main-database transaction, what the trigger does with each:

* not matching (operation, ``when``, an outside row the trigger does not take)
  → nothing;
* a ``bulk`` or ``import`` write → the trigger's write mode for it
  (``bulk_writes`` / ``import_writes``, else the pod's
  ``triggers.bulk_writes_default`` for bulk, else ``skip``):
    - ``skip`` → one aggregated ``trigger_skips`` row per page and reason;
    - ``each`` → fired like a normal write;
    - ``batch`` → one planned batch per page, fired once with the changes as a
      list (``batch:<schedule>:<first>``);
* a normal write → one fire per change (``chg:<xid>:<seq>``).

Fires stop at the daily ceiling: the trigger pauses and the rest of the page's
matches are recorded as ``fire_cap`` skips. Every write is keyed (run source,
skip page, planned batch), so redelivering a page after a crash changes
nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import JsonValue

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter
from app.modules.schedule.domain.match_conditions import evaluate_match_conditions
from app.modules.schedule.domain.schedule import (
    DatastoreScheduleConfig,
    ScheduleEntity,
    TriggerWriteMode,
)
from app.modules.schedule.domain.value_objects import DatastoreOperation
from app.modules.schedule.repositories.trigger_records import TriggerRecords
from app.modules.schedule.services.fire_cap import (
    FIRE_CAP,
    fires_left_today,
    pause_at_fire_cap,
)
from app.modules.schedule.services.trigger_fire import (
    FireOutcome,
    TriggerEvent,
    fire_trigger,
)

BULK_WRITE = "bulk_write"
IMPORT_WRITE = "import_write"


class WriteIntent(StrEnum):
    NORMAL = "normal"
    BULK = "bulk"
    IMPORT = "import"


@dataclass(frozen=True, slots=True, order=True)
class ChangePosition:
    """Where a change sits in its pod's log: ``(xid, seq)``, totally ordered."""

    xid: int
    seq: int

    def __str__(self) -> str:
        return f"{self.xid}:{self.seq}"


@dataclass(frozen=True, slots=True)
class TriggerChange:
    position: ChangePosition
    operation: DatastoreOperation
    record_id: str
    intent: WriteIntent = WriteIntent.NORMAL
    #: The row after the write (before it, for a delete).
    row: Mapping[str, JsonValue] = field(default_factory=dict)
    changed: Sequence[str] | None = None
    previous: Mapping[str, JsonValue] | None = None
    owner_user_id: UUID | None = None
    occurred_at: datetime | None = None
    from_outside: bool = False
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    @property
    def source_event_id(self) -> str:
        return f"chg:{self.position}"


@dataclass(slots=True)
class PageResult:
    fired: int = 0
    filtering: int = 0
    batches: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    paused: bool = False


def write_mode(
    schedule_config: DatastoreScheduleConfig,
    intent: WriteIntent,
    *,
    pod_bulk_default: TriggerWriteMode | None,
) -> TriggerWriteMode:
    """What this trigger does with a change of ``intent``."""
    if intent is WriteIntent.BULK:
        return schedule_config.bulk_writes or pod_bulk_default or TriggerWriteMode.SKIP
    if intent is WriteIntent.IMPORT:
        return schedule_config.import_writes or TriggerWriteMode.SKIP
    return TriggerWriteMode.EACH


def matches(
    schedule: ScheduleEntity, config: DatastoreScheduleConfig, change: TriggerChange
) -> bool:
    if change.from_outside and not schedule.include_outside_rows:
        return False
    if config.operations and change.operation not in config.operations:
        return False
    if not config.when:
        return True
    return evaluate_match_conditions(
        config.when,
        operation=change.operation,
        payload=dict(change.row),
        changed=list(change.changed) if change.changed is not None else None,
        previous=dict(change.previous) if change.previous is not None else None,
    )


async def deliver_page(
    uow: SqlAlchemyUnitOfWork,
    schedule: ScheduleEntity,
    changes: Sequence[TriggerChange],
    *,
    pod_bulk_default: TriggerWriteMode | None = None,
    work: WorkWriter | None = None,
) -> PageResult:
    writer = work or WorkWriter.of(uow)
    result = PageResult()
    config = schedule.datastore_config
    if config is None or not schedule.is_active:
        return result
    records = TriggerRecords(uow.session)
    skips: dict[str, list[TriggerChange]] = {}
    each: list[TriggerChange] = []
    batch: list[TriggerChange] = []
    for change in sorted(changes, key=lambda c: c.position):
        if not matches(schedule, config, change):
            continue
        mode = write_mode(config, change.intent, pod_bulk_default=pod_bulk_default)
        if mode is TriggerWriteMode.SKIP:
            reason = BULK_WRITE if change.intent is WriteIntent.BULK else IMPORT_WRITE
            skips.setdefault(reason, []).append(change)
        elif mode is TriggerWriteMode.BATCH:
            batch.append(change)
        else:
            each.append(change)
    for reason, skipped in skips.items():
        await _record(records, schedule.id, reason, skipped, result)

    budget = await fires_left_today(uow, schedule)
    pending: list[TriggerChange] = []
    for change in each:
        if result.paused:
            pending.append(change)
            continue
        if budget is not None and budget <= 0:
            await pause_at_fire_cap(
                uow, schedule, source_event_id=change.source_event_id, work=writer
            )
            result.paused = True
            pending.append(change)
            continue
        outcome = await fire_trigger(
            uow, schedule, _event(change), work=writer, cap_checked=True
        )
        if outcome is FireOutcome.STARTED:
            result.fired += 1
            budget = None if budget is None else budget - 1
        elif outcome is FireOutcome.FILTERING:
            result.filtering += 1
    if batch:
        if result.paused or (budget is not None and budget <= 0):
            if not result.paused:
                await pause_at_fire_cap(
                    uow, schedule, source_event_id=batch[0].source_event_id, work=writer
                )
                result.paused = True
            pending.extend(batch)
        else:
            await _fire_batch(uow, records, schedule, batch, writer, result)
    if pending:
        # The pause above recorded the change that hit the ceiling; the rest of
        # the page is what the trigger would have fired.
        await _record(records, schedule.id, FIRE_CAP, pending, result)
    return result


async def _fire_batch(
    uow: SqlAlchemyUnitOfWork,
    records: TriggerRecords,
    schedule: ScheduleEntity,
    batch: Sequence[TriggerChange],
    writer: WorkWriter,
    result: PageResult,
) -> None:
    first, last = batch[0].position, batch[-1].position
    planned = await records.plan_batch(
        schedule.id, first=str(first), last=str(last), count=len(batch)
    )
    # A replay reuses the window it already cut, never a differently cut one.
    window = [c for c in batch if _at_or_before(c.position, planned.last)]
    outcome = await fire_trigger(
        uow,
        schedule,
        TriggerEvent(
            source_event_id=f"batch:{schedule.id}:{first}",
            payload={
                "changes": [
                    {
                        "operation": c.operation.value,
                        "record_id": c.record_id,
                        "row": dict(c.row),
                    }
                    for c in window
                ]
            },
            metadata={
                "batch_first": str(first),
                "batch_last": planned.last,
                "count": len(window),
            },
            source_occurred_at=window[-1].occurred_at if window else None,
        ),
        work=writer,
        cap_checked=True,
    )
    if outcome in (FireOutcome.STARTED, FireOutcome.FILTERING):
        result.batches += 1


def _at_or_before(position: ChangePosition, last: str) -> bool:
    xid, _, seq = last.partition(":")
    return position <= ChangePosition(int(xid), int(seq))


async def _record(
    records: TriggerRecords,
    schedule_id: UUID,
    reason: str,
    changes: Sequence[TriggerChange],
    result: PageResult,
) -> None:
    await records.record_skips(
        schedule_id,
        reason=reason,
        first=str(changes[0].position),
        last=str(changes[-1].position),
        count=len(changes),
    )
    result.skipped[reason] = result.skipped.get(reason, 0) + len(changes)


def _event(change: TriggerChange) -> TriggerEvent:
    return TriggerEvent(
        source_event_id=change.source_event_id,
        payload=dict(change.row),
        metadata={
            **dict(change.metadata),
            "record_id": change.record_id,
            "operation": change.operation.value,
            "changed": list(change.changed or []),
            "previous": dict(change.previous or {}),
        },
        user_id=change.owner_user_id,
        source_occurred_at=change.occurred_at,
    )


__all__ = [
    "BULK_WRITE",
    "IMPORT_WRITE",
    "ChangePosition",
    "PageResult",
    "TriggerChange",
    "WriteIntent",
    "deliver_page",
    "matches",
    "write_mode",
]
