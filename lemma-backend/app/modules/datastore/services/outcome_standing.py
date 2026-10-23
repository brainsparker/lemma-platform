"""Where a pod stands against what it owns, counted from its own tables.

A bar is counted week by week: the rows each week holds, and how many of them
met it, with this week's rows that have not met it yet behind the number. A
finish line is counted from its checklist: how many steps there are, how many
are done, how many are past their date, against the days left.

Everything is read as the person asking, through the read-only query path, so
a table with row security counts only the rows they may see -- a pod's work
table is shared for exactly this reason. Anything the outcome names that is
not there comes back as a ``problem`` in plain words, not an error: the page
says what to fix and keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from uuid import UUID

from app.core.authorization.context import Context
from app.core.domain.errors import DomainError
from app.modules.datastore.services.outcome_sql import (
    OutcomeColumnError,
    TableShape,
    checklist_counts,
    checklist_steps,
    rows_in_week,
    rows_in_week_count,
    set_expression,
    week_starts,
    weekly_counts,
)
from app.modules.pod.contracts.outcome import (
    OutcomeChecklist,
    OutcomeKind,
    OutcomeMeasure,
    PodOutcome,
    read_pod_outcome,
)

WEEKS = 8
ROWS_SHOWN = 50
STEPS_SHOWN = 200


@dataclass
class WeekCount:
    starts_on: date
    met: int
    total: int


@dataclass
class StandingRow:
    id: str
    title: str | None
    owner: str | None
    at: datetime | None


@dataclass
class BarStanding:
    shape: str
    target: float
    label: str
    weeks: list[WeekCount]
    rows: list[StandingRow]
    rows_count: int


@dataclass
class FinishStep:
    id: str
    title: str | None
    owner: str | None
    due_on: date | None
    done: bool
    behind: bool


@dataclass
class FinishStanding:
    due_on: date
    since: date
    today: date
    days_left: int
    days_total: int
    total: int
    done: int
    behind: int
    steps: list[FinishStep] = field(default_factory=list)


@dataclass
class OutcomeStanding:
    outcome: PodOutcome | None
    bar: BarStanding | None = None
    finish: FinishStanding | None = None
    problem: str | None = None


async def outcome_standing(
    session,
    *,
    pod_id: UUID,
    user_id: UUID,
    ctx: Context,
    record_service,
    table_service,
    today: date | None = None,
) -> OutcomeStanding:
    """Where the pod stands today, or what stops it being counted."""
    outcome = await read_pod_outcome(session, pod_id)
    if outcome is None:
        return OutcomeStanding(outcome=None)
    today = today or datetime.now(timezone.utc).date()
    reader = _Reader(
        record_service, table_service, pod_id=pod_id, user_id=user_id, ctx=ctx
    )
    try:
        if outcome.kind == OutcomeKind.BAR and outcome.measure is not None:
            bar = await _bar(reader, outcome.measure, today)
            return OutcomeStanding(outcome=outcome, bar=bar)
        if outcome.kind == OutcomeKind.FINISH and outcome.checklist is not None:
            finish = await _finish(reader, outcome, outcome.checklist, today)
            return OutcomeStanding(outcome=outcome, finish=finish)
    except OutcomeColumnError as exc:
        return OutcomeStanding(outcome=outcome, problem=str(exc))
    except _Unreadable as exc:
        return OutcomeStanding(outcome=outcome, problem=str(exc))
    return OutcomeStanding(outcome=outcome)


class _Unreadable(Exception):
    """A table the outcome names that this person cannot read, or that is gone."""


class _Reader:
    def __init__(
        self,
        record_service,
        table_service,
        *,
        pod_id: UUID,
        user_id: UUID,
        ctx: Context,
    ):
        self._records = record_service
        self._pod_id = pod_id
        self._user_id = user_id
        self._ctx = ctx
        self._tables = table_service

    async def shape(self, table_name: str) -> TableShape:
        try:
            table = await self._tables.get_table(self._pod_id, table_name, self._ctx)
        except DomainError as exc:
            if getattr(exc, "status_code", None) in (403, 404):
                raise _Unreadable(
                    f"The table {table_name} is not here, or you can't read it."
                ) from exc
            raise
        columns = {column.name: column.type.value for column in table.columns}
        columns.setdefault("created_at", "DATETIME")
        columns.setdefault("updated_at", "DATETIME")
        if table.primary_key_column == "id":
            columns.setdefault("id", "UUID")
        return TableShape(
            name=table.table_name,
            primary_key=table.primary_key_column,
            columns=columns,
        )

    async def rows(self, query: str) -> list[dict]:
        rows, _total, _truncated = await self._records.execute_readonly_query(
            pod_id=self._pod_id,
            query=query,
            user_id=self._user_id,
            table_service=self._tables,
            ctx=self._ctx,
        )
        return [dict(row) for row in rows]


async def _bar(reader: _Reader, measure: OutcomeMeasure, today: date) -> BarStanding:
    shape = await reader.shape(measure.table)
    met = (
        set_expression(shape, measure.met_column, what="met column")
        if measure.shape == "share" and measure.met_column
        else None
    )
    starts = week_starts(today, WEEKS)
    counted = (
        await reader.rows(
            weekly_counts(
                shape, time_column=measure.time_column, met=met, starts=starts
            )
        )
    )[0]
    weeks = [
        WeekCount(
            starts_on=start,
            total=int(counted.get(f"total_{index}") or 0),
            met=int(counted.get(f"met_{index}") or 0)
            if met is not None
            else int(counted.get(f"total_{index}") or 0),
        )
        for index, start in enumerate(starts)
    ]
    this_week = starts[-1]
    found = await reader.rows(
        rows_in_week(
            shape,
            time_column=measure.time_column,
            met=met,
            start=this_week,
            title_column=measure.title_column,
            owner_column=measure.owner_column,
            limit=ROWS_SHOWN,
        )
    )
    count = (
        await reader.rows(
            rows_in_week_count(
                shape, time_column=measure.time_column, met=met, start=this_week
            )
        )
    )[0]
    return BarStanding(
        shape=measure.shape,
        target=measure.target,
        label=measure.label,
        weeks=weeks,
        rows=[_row(row) for row in found],
        rows_count=int(count.get("n") or 0),
    )


async def _finish(
    reader: _Reader, outcome: PodOutcome, checklist: OutcomeChecklist, today: date
) -> FinishStanding:
    shape = await reader.shape(checklist.table)
    done = set_expression(shape, checklist.done_column, what="done column")
    counts = (
        await reader.rows(
            checklist_counts(
                shape, done=done, due_column=checklist.due_column, today=today
            )
        )
    )[0]
    found = await reader.rows(
        checklist_steps(
            shape,
            done=done,
            title_column=checklist.title_column,
            owner_column=checklist.owner_column,
            due_column=checklist.due_column,
            limit=STEPS_SHOWN,
        )
    )
    due_on = outcome.due_on or today
    since = outcome.since or today
    steps = [
        FinishStep(
            id=str(row.get("row_id")),
            title=row.get("title"),
            owner=row.get("owner"),
            due_on=_as_date(row.get("due_on")),
            done=bool(row.get("done")),
            behind=not row.get("done")
            and _as_date(row.get("due_on")) is not None
            and _as_date(row.get("due_on")) < today,
        )
        for row in found
    ]
    rows_total = int(counts.get("total") or 0)
    return FinishStanding(
        due_on=due_on,
        since=since,
        today=today,
        days_left=max(0, (due_on - today).days),
        days_total=max(1, (due_on - since).days),
        total=checklist.target or rows_total,
        done=int(counts.get("done") or 0),
        behind=int(counts.get("behind") or 0),
        steps=sorted(steps, key=lambda step: (step.done, not step.behind)),
    )


def _row(row: dict) -> StandingRow:
    at = row.get("at")
    if isinstance(at, date) and not isinstance(at, datetime):
        at = datetime(at.year, at.month, at.day, tzinfo=timezone.utc)
    return StandingRow(
        id=str(row.get("row_id")),
        title=row.get("title"),
        owner=row.get("owner"),
        at=at,
    )


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
