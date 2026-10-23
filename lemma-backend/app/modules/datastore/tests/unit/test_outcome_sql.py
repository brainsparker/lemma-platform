"""The statements that count where a pod stands: only real columns, quoted."""

from __future__ import annotations

from datetime import date

import pytest
import sqlglot

from app.modules.datastore.services.outcome_sql import (
    OutcomeColumnError,
    TableShape,
    checklist_counts,
    checklist_steps,
    rows_in_week,
    set_expression,
    week_starts,
    weekly_counts,
)

CONVERSATIONS = TableShape(
    name="conversations",
    primary_key="id",
    columns={
        "id": "UUID",
        "customer": "TEXT",
        "opened_at": "DATETIME",
        "answered_at": "DATETIME",
        "answered_in_time": "BOOLEAN",
        "with_person": "TEXT",
        "created_at": "DATETIME",
    },
)


def test_weeks_start_on_mondays_and_end_with_this_one():
    starts = week_starts(date(2026, 10, 21), 8)
    assert starts[-1] == date(2026, 10, 19)
    assert starts[0] == date(2026, 8, 31)
    assert all(day.weekday() == 0 for day in starts)


def test_a_yes_no_column_is_met_when_true_and_any_other_when_set():
    assert (
        set_expression(CONVERSATIONS, "answered_in_time", what="met")
        == '"answered_in_time" IS TRUE'
    )
    assert (
        set_expression(CONVERSATIONS, "answered_at", what="met")
        == '"answered_at" IS NOT NULL'
    )


def test_weekly_counts_is_one_postgres_statement_over_quoted_columns():
    met = set_expression(CONVERSATIONS, "answered_in_time", what="met")
    query = weekly_counts(
        CONVERSATIONS,
        time_column="opened_at",
        met=met,
        starts=week_starts(date(2026, 10, 21), 2),
    )
    parsed = sqlglot.parse(query, read="postgres")
    assert len(parsed) == 1
    assert "\"opened_at\" >= '2026-10-12'" in query
    assert "met_1" in query and "total_1" in query


def test_a_column_the_table_does_not_have_is_refused_by_name():
    with pytest.raises(OutcomeColumnError, match="no column replied_at"):
        weekly_counts(
            CONVERSATIONS,
            time_column="replied_at",
            met=None,
            starts=[date(2026, 10, 19)],
        )


def test_a_hostile_name_never_reaches_the_statement():
    with pytest.raises(OutcomeColumnError):
        rows_in_week(
            CONVERSATIONS,
            time_column='opened_at"; DROP TABLE x; --',
            met=None,
            start=date(2026, 10, 19),
            title_column=None,
            owner_column=None,
            limit=5,
        )


def test_this_weeks_rows_behind_a_share_are_the_ones_not_met():
    met = set_expression(CONVERSATIONS, "answered_in_time", what="met")
    query = rows_in_week(
        CONVERSATIONS,
        time_column="opened_at",
        met=met,
        start=date(2026, 10, 19),
        title_column="customer",
        owner_column="with_person",
        limit=50,
    )
    assert 'NOT coalesce("answered_in_time" IS TRUE, false)' in query
    assert query.endswith("LIMIT 50")
    sqlglot.parse_one(query, read="postgres")


def test_a_checklist_counts_done_and_behind_and_lists_what_is_left_first():
    steps = TableShape(
        name="steps",
        primary_key="id",
        columns={
            "id": "UUID",
            "step": "TEXT",
            "owner": "TEXT",
            "due_on": "DATE",
            "done": "BOOLEAN",
            "created_at": "DATETIME",
        },
    )
    done = set_expression(steps, "done", what="done")
    counts = checklist_counts(
        steps, done=done, due_column="due_on", today=date(2026, 10, 21)
    )
    assert "\"due_on\" < '2026-10-21'" in counts
    listed = checklist_steps(
        steps,
        done=done,
        title_column="step",
        owner_column="owner",
        due_column="due_on",
        limit=200,
    )
    assert (
        'ORDER BY coalesce("done" IS TRUE, false) ASC, "due_on" ASC NULLS LAST'
        in listed
    )
    for query in (counts, listed):
        sqlglot.parse_one(query, read="postgres")
