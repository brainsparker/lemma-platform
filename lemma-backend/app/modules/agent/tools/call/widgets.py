"""A canvas item, drawn: its kind's template and the data it reads.

Rendered when the widget is served, not when it is drawn, so the stored tool call
holds only what the agent wrote and a template fix reaches old items too.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import JsonValue

_TEMPLATES = Path(__file__).parent / "templates"
#: Kinds served as a widget built from a template. `file` is a pod file shown as
#: itself and `widget` is the agent's own HTML; neither needs one.
TEMPLATE_KINDS = frozenset({"diagram", "chart", "note", "image"})
_DATA_FIELDS = ("title", "caption", "mermaid", "chart", "markdown", "url")


@lru_cache(maxsize=8)  # one per template file
def _template(name: str) -> str:
    return (_TEMPLATES / f"{name}.html").read_text(encoding="utf-8")


def canvas_widget(
    args: dict[str, JsonValue],
) -> tuple[str, dict[str, JsonValue]] | None:
    """The widget HTML and ``lemma.data`` for a ``show_on_canvas`` call, or None
    when its kind is not drawn from a template."""
    kind = args.get("kind")
    if not isinstance(kind, str):
        return None
    if kind == "widget":
        html = args.get("html")
        return (html, {}) if isinstance(html, str) else None
    if kind not in TEMPLATE_KINDS:
        return None
    data = {field: args[field] for field in _DATA_FIELDS if field in args}
    return _template("_frame") + _template(kind), data
