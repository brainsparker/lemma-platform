"""What a pod owns: a bar held every week, or a finish line by a date."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.pod.contracts.members import _owns
from app.modules.pod.domain.pod_entities import PodConfig, PodOutcome

BAR = {
    "sentence": "Every customer hears back within a day.",
    "kind": "bar",
    "measure": {
        "table": "conversations",
        "time_column": "opened_at",
        "met_column": "answered_in_time",
        "target": 0.9,
        "label": "heard back within a day",
    },
}
FINISH = {
    "sentence": "The launch ships on November 18.",
    "kind": "finish",
    "due_on": "2026-11-18",
    "checklist": {"table": "steps", "done_column": "done"},
}


def test_a_bar_and_a_finish_line_both_read():
    assert PodOutcome.model_validate(BAR).measure.table == "conversations"
    assert PodOutcome.model_validate(FINISH).due_on.isoformat() == "2026-11-18"


def test_a_finish_line_needs_its_date_and_a_bar_has_none():
    with pytest.raises(ValidationError, match="needs due_on"):
        PodOutcome.model_validate({**FINISH, "due_on": None})
    with pytest.raises(ValidationError, match="holds every week"):
        PodOutcome.model_validate({**BAR, "due_on": "2026-11-18"})


def test_a_share_needs_what_meets_it_and_a_fraction_for_a_target():
    with pytest.raises(ValidationError, match="met_column"):
        PodOutcome.model_validate(
            {**BAR, "measure": {**BAR["measure"], "met_column": None}}
        )
    with pytest.raises(ValidationError, match="at most 1"):
        PodOutcome.model_validate({**BAR, "measure": {**BAR["measure"], "target": 90}})


def test_an_outcome_can_still_be_working_out_how_it_is_counted():
    outcome = PodOutcome.model_validate(
        {"sentence": "Every invoice is paid within 30 days.", "kind": "bar"}
    )
    assert outcome.measure is None


def test_a_config_without_an_outcome_stays_as_it_was():
    assert "outcome" not in PodConfig().model_dump(mode="json")
    assert (
        PodConfig.model_validate({"outcome": BAR}).model_dump(mode="json")["outcome"][
            "kind"
        ]
        == "bar"
    )


def test_the_agent_is_told_what_it_owns_and_which_table_counts_it():
    said = _owns({"outcome": BAR})
    assert said.startswith("Every customer hears back within a day.")
    assert "`conversations`" in said and "90%" in said
    assert "`steps`" in _owns({"outcome": FINISH})
    assert _owns({"outcome": {**FINISH, "finished_on": "2026-11-18"}}) is None
    assert "not set yet" in _owns({"outcome": {"sentence": "Ship it.", "kind": "bar"}})
