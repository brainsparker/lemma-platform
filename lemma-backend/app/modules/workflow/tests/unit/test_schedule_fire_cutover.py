"""The cutover's schedule-fire converter starts what the old job would have
started, and drops a legacy timer fire for something already waiting."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.workflow.events import legacy_cutover


@pytest.fixture
def starts(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def record(**kwargs) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(legacy_cutover, "start_legacy_fire", record)
    return calls


async def test_an_owed_fire_starts_its_target_once_per_delivery(starts) -> None:
    schedule_id = str(uuid4())
    await legacy_cutover.schedule_fired(
        None,  # type: ignore[arg-type]
        {
            "event_type": "schedule.fired",
            "schedule_id": schedule_id,
            "user_id": None,
            "payload": {"a": 1},
            "source_event_id": "tick:1",
            "scheduled_at": "2026-10-14T10:00:00+00:00",
        },
        "workflow.schedule-start",
    )
    assert starts == [
        {
            "schedule_id": schedule_id,
            "user_id": None,
            "payload": {"a": 1},
            "metadata": {},
            "llm_output": None,
            "schedule_event_id": "tick:1",
            "source_occurred_at": "2026-10-14T10:00:00+00:00",
        }
    ]


@pytest.mark.parametrize("key", ["workflow_run_id", "conversation_id"])
async def test_a_legacy_timer_fire_is_dropped(starts, key) -> None:
    """Workflow and agent timers are items of their own now, seeded for every
    active wait; the old fire must not be read as a schedule."""
    event = {"schedule_id": str(uuid4()), "payload": {key: str(uuid4())}}
    await legacy_cutover.schedule_fired(None, event, "workflow.schedule-start")  # type: ignore[arg-type]
    await legacy_cutover.schedule_job(
        None,  # type: ignore[arg-type]
        {"kwargs": {"schedule_id": str(uuid4()), "payload": {key: "x"}}},
        "streaq:check_and_start_flows_for_schedule",
    )
    assert starts == []
