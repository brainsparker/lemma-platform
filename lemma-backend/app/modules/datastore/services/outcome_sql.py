"""The statements that say where a pod stands against what it owns.

A pod's outcome names one of its tables and a few of that table's columns:
which column dates a row into a week, which says a row met the bar, which a
step is done by. These build the read-only statements that count them, for
:mod:`outcome_standing` to run through the datastore's read-only query path,
which applies row security and the deployment's cost guard.

That path takes no bind parameters, so nothing here is ever interpolated from
a person's words: every column is checked against the table's own columns and
double-quoted, and every value is a date this module wrote itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

BOOLEAN = "BOOLEAN"


class OutcomeColumnError(ValueError):
    """The outcome names a column its table does not have."""


@dataclass(frozen=True, slots=True)
class TableShape:
    """The columns a table really has, by name, with their types."""

    name: str
    primary_key: str
    columns: dict[str, str]

    def column(self, name: str | None, *, what: str) -> str | None:
        """``name`` double-quoted, after checking the table has it."""
        if name is None:
            return None
        if name not in self.columns:
            raise OutcomeColumnError(
                f"The table {self.name} has no column {name} (its {what})."
            )
        return _quote(name)

    def is_boolean(self, name: str) -> bool:
        return self.columns.get(name) == BOOLEAN

    @property
    def table(self) -> str:
        return _quote(self.name)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _day(value: date) -> str:
    return "'" + value.isoformat() + "'"


def set_expression(shape: TableShape, column: str, *, what: str) -> str:
    """A row counts as met (or done) when the column is set: true for a
    yes/no column, anything at all for the rest."""
    quoted = shape.column(column, what=what)
    return f"{quoted} IS TRUE" if shape.is_boolean(column) else f"{quoted} IS NOT NULL"


def week_starts(today: date, weeks: int) -> list[date]:
    """The Mondays of the last ``weeks`` weeks, oldest first, ending with this
    week's."""
    monday = today - timedelta(days=today.weekday())
    return [monday - timedelta(weeks=back) for back in range(weeks - 1, -1, -1)]


def weekly_counts(
    shape: TableShape,
    *,
    time_column: str,
    met: str | None,
    starts: list[date],
) -> str:
    """One row: for each week, how many rows fell in it and how many met."""
    when = shape.column(time_column, what="time column")
    parts: list[str] = []
    for index, start in enumerate(starts):
        window = (
            f"{when} >= {_day(start)} AND {when} < {_day(start + timedelta(weeks=1))}"
        )
        parts.append(f"count(*) FILTER (WHERE {window}) AS total_{index}")
        if met is not None:
            parts.append(f"count(*) FILTER (WHERE {window} AND {met}) AS met_{index}")
    since = _day(starts[0])
    return f"SELECT {', '.join(parts)} FROM {shape.table} WHERE {when} >= {since}"


def rows_in_week(
    shape: TableShape,
    *,
    time_column: str,
    met: str | None,
    start: date,
    title_column: str | None,
    owner_column: str | None,
    limit: int,
) -> str:
    """This week's rows behind the number: those not met yet, oldest first --
    or, with no bar to meet, every row the week counted."""
    when = shape.column(time_column, what="time column")
    title = shape.column(title_column, what="title column")
    owner = shape.column(owner_column, what="owner column")
    key = shape.column(shape.primary_key, what="primary key")
    window = f"{when} >= {_day(start)} AND {when} < {_day(start + timedelta(weeks=1))}"
    condition = window if met is None else f"{window} AND NOT coalesce({met}, false)"
    picked = [
        f"{key}::text AS row_id",
        f"{title}::text AS title" if title else "NULL AS title",
        f"{owner}::text AS owner" if owner else "NULL AS owner",
        f"{when} AS at",
    ]
    return (
        f"SELECT {', '.join(picked)} FROM {shape.table} WHERE {condition} "
        f"ORDER BY {when} ASC LIMIT {int(limit)}"
    )


def rows_in_week_count(
    shape: TableShape, *, time_column: str, met: str | None, start: date
) -> str:
    when = shape.column(time_column, what="time column")
    window = f"{when} >= {_day(start)} AND {when} < {_day(start + timedelta(weeks=1))}"
    condition = window if met is None else f"{window} AND NOT coalesce({met}, false)"
    return f"SELECT count(*) AS n FROM {shape.table} WHERE {condition}"


def checklist_counts(
    shape: TableShape, *, done: str, due_column: str | None, today: date
) -> str:
    """How many steps there are, how many are done, and how many are past
    their date without being done."""
    due = shape.column(due_column, what="due column")
    behind = (
        f"count(*) FILTER (WHERE NOT coalesce({done}, false) AND {due} < {_day(today)})"
        if due
        else "0"
    )
    return (
        f"SELECT count(*) AS total, count(*) FILTER (WHERE {done}) AS done, "
        f"{behind} AS behind FROM {shape.table}"
    )


def checklist_steps(
    shape: TableShape,
    *,
    done: str,
    title_column: str | None,
    owner_column: str | None,
    due_column: str | None,
    limit: int,
) -> str:
    """The steps, the ones still to do first and the soonest due first."""
    title = shape.column(title_column, what="title column")
    owner = shape.column(owner_column, what="owner column")
    due = shape.column(due_column, what="due column")
    key = shape.column(shape.primary_key, what="primary key")
    picked = [
        f"{key}::text AS row_id",
        f"{title}::text AS title" if title else "NULL AS title",
        f"{owner}::text AS owner" if owner else "NULL AS owner",
        f"{due}::date AS due_on" if due else "NULL AS due_on",
        f"coalesce({done}, false) AS done",
    ]
    order = [f"coalesce({done}, false) ASC"]
    if due:
        order.append(f"{due} ASC NULLS LAST")
    order.append('"created_at" ASC')
    return (
        f"SELECT {', '.join(picked)} FROM {shape.table} "
        f"ORDER BY {', '.join(order)} LIMIT {int(limit)}"
    )
