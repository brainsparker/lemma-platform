"""What the call tools take.

``show_on_canvas`` takes data, not markup, for every kind but ``widget``: a
Mermaid diagram, rows and a shape, markdown. The server turns each into a widget
from a template at serve time. Models that cannot be trusted to write working
HTML write these reliably, and a template fixed later fixes every item drawn
with it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, JsonValue

from app.modules.agent.tools.context import BaseToolResponse

CanvasKind = Literal["diagram", "chart", "note", "file", "image", "widget"]
ChartShape = Literal["bars", "line", "scatter", "stats", "table"]
ValueFormat = Literal["num", "money", "pct", "date"]
Urgency = Literal["context", "when_idle", "now"]

MAX_TEXT_CHARS = 20_000
MAX_ROWS = 500

#: The field each kind is drawn from. Exactly that one is required.
SOURCE_FIELD: dict[str, str] = {
    "diagram": "mermaid",
    "chart": "chart",
    "note": "markdown",
    "file": "path",
    "image": "url",
    "widget": "html",
}


class ChartSpec(BaseModel):
    shape: ChartShape = Field(
        description=(
            "bars: one measure across categories (x = category key, y = value "
            "key). line: a measure over time (x = time key, y = one key or "
            "several). scatter: two measures (x, y). stats: key numbers, rows of "
            "{label, value, previous?}. table: records to read down."
        )
    )
    rows: list[dict[str, JsonValue]] = Field(
        max_length=MAX_ROWS, description="The data, one object per row."
    )
    x: str | None = Field(default=None, description="The key on the x axis.")
    y: str | list[str] | None = Field(
        default=None, description="The value key, or several for a multi-line chart."
    )
    format: ValueFormat | None = Field(
        default=None, description="How values read: num, money, pct or date."
    )
    finding: str | None = Field(
        default=None,
        max_length=300,
        description="The one sentence the chart proves, shown above it.",
    )


class ShowOnCanvasRequest(BaseModel):
    kind: CanvasKind = Field(
        description=(
            "diagram (Mermaid source in `mermaid`), chart (`chart`), note "
            "(markdown in `markdown`: a claim, bullets, a small table), file (a "
            "pod `path`: an image, PDF or document), image (an https `url`), "
            "widget (`html`, a widget fragment, only when nothing else fits)."
        )
    )
    title: str = Field(min_length=1, max_length=120, description="A short title.")
    caption: str | None = Field(
        default=None,
        max_length=300,
        description="One line saying what this shows, for the voice to refer to.",
    )
    mermaid: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    chart: ChartSpec | None = None
    markdown: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    path: str | None = Field(default=None, max_length=1024)
    url: str | None = Field(default=None, max_length=2048)
    html: str | None = Field(default=None, max_length=200_000)
    replaces: str | None = Field(
        default=None,
        max_length=255,
        description="The item_id of an earlier item this one replaces.",
    )


class ShowOnCanvasResponse(BaseToolResponse):
    item_id: str | None = None


class RelayToVoiceRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=4000,
        description="What the voice should know or say, as notes, not a script.",
    )
    urgency: Urgency = Field(
        default="when_idle",
        description=(
            "context: known, not said now. when_idle: said at the next pause. "
            "now: said as soon as the caller stops talking -- failures and "
            "questions only."
        ),
    )


class RelayToVoiceResponse(BaseToolResponse):
    pass


def canvas_problem(request: ShowOnCanvasRequest) -> str | None:
    """Why the request cannot be drawn, or None."""
    wanted = SOURCE_FIELD[request.kind]
    given = [field for field in SOURCE_FIELD.values() if getattr(request, field)]
    if wanted not in given:
        return f"kind '{request.kind}' needs `{wanted}`."
    extra = [field for field in given if field != wanted]
    if extra:
        return f"kind '{request.kind}' takes only `{wanted}`; drop {', '.join(extra)}."
    if request.kind == "image" and not (request.url or "").startswith("https://"):
        return "An image `url` must be https."
    if request.kind == "chart" and request.chart is not None:
        return _chart_problem(request.chart)
    return None


def _chart_problem(chart: ChartSpec) -> str | None:
    if not chart.rows:
        return "A chart needs at least one row."
    if chart.shape in {"bars", "line", "scatter"} and not (chart.x and chart.y):
        return f"A {chart.shape} chart needs `x` and `y`."
    keys = set().union(*(row.keys() for row in chart.rows))
    named = [chart.x] if chart.x else []
    named += [chart.y] if isinstance(chart.y, str) else list(chart.y or [])
    missing = [key for key in named if key not in keys]
    if missing:
        return f"No row has {', '.join(missing)}; the rows' keys are {sorted(keys)}."
    return None
