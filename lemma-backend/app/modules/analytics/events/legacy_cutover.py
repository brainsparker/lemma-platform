"""The cutover's converter for product analytics the old transport still owed.

The eleven ``analytics.*`` consumers projected committed domain events into
product analytics; the after-commit sink does it now for events committed in
the process. An event the old transport still owed reaches the same
projections here, under its own origin, once per old consumer (the routes'
event types do not overlap). Worker only.
"""

from __future__ import annotations


from app.core.analytics.domain_events import project_payload
from app.core.infrastructure.cutover.routes import (
    ROUTES,
    LegacyPayload,
    legacy_converter,
)
from app.core.infrastructure.work import JobContext

ANALYTICS_ROUTES = tuple(
    route.consumer for route in ROUTES if route.consumer.startswith("analytics.")
)


@legacy_converter(*ANALYTICS_ROUTES)
async def analytics_event(ctx: JobContext, event: LegacyPayload, route: str) -> None:
    del ctx, route
    await project_payload(event)
