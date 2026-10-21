"""The tools a run in a live call gets: the canvas, and -- for the coordinator --
the voice.

Both are recorded, not delivered, here. A tool call is already a persisted
message on the conversation's stream; the call session on the API listens to
that stream and does the delivering, so the run needs no route to the replica
holding the caller's socket.
"""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.widget_html_validation import validate_widget_html
from app.modules.agent.domain.calls import CallRole
from app.modules.agent.tools.call.models import (
    RelayToVoiceRequest,
    RelayToVoiceResponse,
    ShowOnCanvasRequest,
    ShowOnCanvasResponse,
    canvas_problem,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.user_interaction.models import (
    DisplayResourceRequest,
    DisplayResourceType,
    validate_display_payload,
)


async def show_on_canvas(
    ctx: RunContext[BaseAgentContext], request: ShowOnCanvasRequest
) -> ShowOnCanvasResponse:
    """Put something on the call's shared canvas, beside the conversation.

    The newest item takes the person's screen. Give data, not markup:
    `diagram` takes Mermaid source (flowchart, sequence, timeline, mindmap,
    gantt...), `chart` takes rows and a shape, `note` takes markdown, `file` a
    pod path (images, PDFs, documents), `image` an https URL. `widget` takes a
    widget HTML fragment, for what none of those can show.

    Returns the item's `item_id`; pass it as `replaces` to update that item
    instead of adding a near-copy. If the person's screen cannot draw an item,
    you are told, and can fix it with `replaces`.
    """
    problem = canvas_problem(request)
    if problem is None and request.kind == "file":
        problem = validate_display_payload(
            DisplayResourceRequest(type=DisplayResourceType.FILE, path=request.path)
        )
    if problem is None and request.kind == "widget" and request.html:
        errors = validate_widget_html(request.html)
        problem = " ".join(errors) if errors else None
    if problem is not None:
        return ShowOnCanvasResponse(success=False, error=problem)
    return ShowOnCanvasResponse(
        success=True,
        message="On the canvas now.",
        item_id=ctx.tool_call_id,
    )


async def relay_to_voice(
    ctx: RunContext[BaseAgentContext], request: RelayToVoiceRequest
) -> RelayToVoiceResponse:
    """Give the call's voice something to know or to say.

    Write notes, not a script: the voice says it in its own words. `context`
    is known but not said now; `when_idle` is said at the next pause; `now` is
    said as soon as the person stops talking, for failures and questions only.
    """
    del ctx
    spoken = "will be said" if request.urgency != "context" else "is known to the voice"
    return RelayToVoiceResponse(success=True, message=f"Relayed; it {spoken}.")


_worker_toolset = FunctionToolset[BaseAgentContext](tools=[show_on_canvas])
_coordinator_toolset = FunctionToolset[BaseAgentContext](
    tools=[show_on_canvas, relay_to_voice]
)


def call_toolset(role: CallRole) -> FunctionToolset[BaseAgentContext]:
    """Every run in a call draws; only the coordinator speaks."""
    return _coordinator_toolset if role is CallRole.COORDINATOR else _worker_toolset
