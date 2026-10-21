"""A run in a live call: the canvas and the voice, and who gets which."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.widget_html_validation import validate_widget_html
from app.modules.agent.domain.calls import CallMark, CallRole, call_mark
from app.modules.agent.tools.call.models import (
    ChartSpec,
    ShowOnCanvasRequest,
    canvas_problem,
)
from app.modules.agent.tools.call.pydantic_adapter import (
    call_toolset,
    relay_to_voice,
    show_on_canvas,
)
from app.modules.agent.tools.call.models import RelayToVoiceRequest
from app.modules.agent.tools.call.widgets import canvas_widget
from app.modules.agent.tools.tool_assembler import _call_toolsets

pytestmark = pytest.mark.unit


def _ctx(tool_call_id: str = "call-1") -> SimpleNamespace:
    return SimpleNamespace(tool_call_id=tool_call_id, deps=SimpleNamespace())


def test_a_call_mark_round_trips_and_a_worker_inherits_the_call() -> None:
    mark = CallMark(call_id=uuid4(), role=CallRole.COORDINATOR)
    assert call_mark({"call": mark.as_metadata()}) == mark
    assert mark.for_worker() == CallMark(call_id=mark.call_id, role=CallRole.WORKER)
    assert call_mark({"call": {"call_id": "nope", "role": "coordinator"}}) is None
    assert call_mark({"call": "x"}) is None
    assert call_mark(None) is None


def test_only_the_coordinator_speaks_but_everyone_in_the_call_draws() -> None:
    def names(role: CallRole) -> set[str]:
        return set(call_toolset(role).tools)

    assert names(CallRole.COORDINATOR) == {"show_on_canvas", "relay_to_voice"}
    assert names(CallRole.WORKER) == {"show_on_canvas"}


def test_the_call_tools_come_with_the_call_mark_not_with_configuration() -> None:
    def conversation(metadata: dict[str, object] | None) -> SimpleNamespace:
        return SimpleNamespace(metadata=metadata)

    assert _call_toolsets(None) == []
    assert _call_toolsets(conversation({})) == []
    mark = CallMark(call_id=uuid4(), role=CallRole.WORKER)
    [toolset] = _call_toolsets(conversation({"call": mark.as_metadata()}))
    assert set(toolset.tools) == {"show_on_canvas"}  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("request_fields", "problem"),
    [
        ({"kind": "diagram"}, "needs `mermaid`"),
        (
            {"kind": "diagram", "mermaid": "flowchart LR; A-->B", "markdown": "x"},
            "takes only `mermaid`",
        ),
        ({"kind": "image", "url": "http://example.com/a.png"}, "must be https"),
        (
            {
                "kind": "chart",
                "chart": ChartSpec(
                    shape="bars", rows=[{"venue": "A", "price": 1}], x="venue"
                ),
            },
            "needs `x` and `y`",
        ),
        (
            {
                "kind": "chart",
                "chart": ChartSpec(
                    shape="bars", rows=[{"venue": "A"}], x="venue", y="price"
                ),
            },
            "No row has price",
        ),
        (
            {"kind": "chart", "chart": ChartSpec(shape="table", rows=[])},
            "at least one row",
        ),
    ],
)
def test_a_canvas_request_says_exactly_what_is_wrong(
    request_fields: dict[str, object], problem: str
) -> None:
    found = canvas_problem(ShowOnCanvasRequest(title="t", **request_fields))
    assert found is not None and problem in found


async def test_a_drawn_item_answers_with_its_id_and_a_refused_one_with_the_reason() -> (
    None
):
    shown = await show_on_canvas(
        _ctx("tc-9"), ShowOnCanvasRequest(kind="note", title="Agenda", markdown="- one")
    )  # type: ignore[arg-type]
    assert shown.success and shown.item_id == "tc-9"
    refused = await show_on_canvas(
        _ctx(),
        ShowOnCanvasRequest(
            kind="file", title="Leak", path="/Users/someone/secret.txt"
        ),
    )  # type: ignore[arg-type]
    assert not refused.success and refused.error
    bad_html = await show_on_canvas(
        _ctx(),
        ShowOnCanvasRequest(
            kind="widget", title="x", html="<!doctype html><html></html>"
        ),
    )  # type: ignore[arg-type]
    assert not bad_html.success


async def test_a_relay_is_acknowledged_by_whether_it_will_be_said() -> None:
    said = await relay_to_voice(
        _ctx(), RelayToVoiceRequest(text="found three", urgency="when_idle")
    )  # type: ignore[arg-type]
    known = await relay_to_voice(
        _ctx(), RelayToVoiceRequest(text="started", urgency="context")
    )  # type: ignore[arg-type]
    assert said.success and "will be said" in (said.message or "")
    assert known.success and "known to the voice" in (known.message or "")


@pytest.mark.parametrize("kind", ["diagram", "chart", "note", "image"])
def test_every_template_is_a_valid_widget_carrying_only_what_it_draws(
    kind: str,
) -> None:
    drawn = canvas_widget(
        {
            "kind": kind,
            "title": "T",
            "caption": "C",
            "mermaid": "flowchart LR; A-->B",
            "extra": "no",
        }
    )
    assert drawn is not None
    html, data = drawn
    assert validate_widget_html(html) == []
    assert data["title"] == "T" and "extra" not in data
    assert "lemma-canvas-error" in html


def test_files_are_shown_as_themselves_and_html_widgets_as_written() -> None:
    assert canvas_widget({"kind": "file", "path": "/me/a.pdf"}) is None
    assert canvas_widget({"kind": "widget", "html": "<p>hi</p>"}) == ("<p>hi</p>", {})
    assert (
        canvas_widget({"type": "WIDGET", "content": "<p>x</p>"}) is None
    )  # display_resource's own args
