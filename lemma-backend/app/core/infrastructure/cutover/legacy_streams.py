"""The legacy Redis streams and consumer groups.

Every group the old transport ever declared (``LEGACY_GROUPS``). Nothing reads
them any more: what they still owed is read from the outbox and the inbox,
which outlive every entry a group could still deliver (``routes.py``). The
cutover check reports their backlog; the sweep destroys the groups and
deletes the streams once the old release is gone.
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

#: (stream, group) for every consumer group the old transport declared.
LEGACY_GROUPS: tuple[tuple[str, str], ...] = (
    ("agent_events", "agent-events"),
    ("agent_events", "analytics-agent"),
    ("agent_events", "schedule-agent-outcomes"),
    ("agent_events", "workflow-agent-events"),
    ("app_events", "analytics-app"),
    ("connector_events", "analytics-connector"),
    ("datastore.events", "agent-memory-brief-invalidation"),
    ("datastore.events", "analytics-datastore"),
    ("datastore.events", "datastore-file-events"),
    ("datastore.events", "mcp-access-event-deliveries"),
    ("datastore.events", "schedule-datastore-events"),
    ("function_run_events", "workflow-function-events"),
    ("function.events", "analytics-function"),
    ("identity_events", "analytics-identity"),
    ("identity_events", "identity-email-events"),
    ("identity_events", "identity-mobile-verification-events"),
    ("identity_events", "surface-identity-events"),
    ("pod_bundle_events", "analytics-pod-bundle"),
    ("pod_events", "agent-pod-links-pod-events"),
    ("pod_events", "analytics-pod"),
    ("pod_events", "mcp-access-pod-events"),
    ("pod_events", "pod-join-request-events"),
    ("pod_events", "pod-provisioning-events"),
    ("pod_events", "schedule-pod-events"),
    ("pod_events", "surface-pod-deletion-events"),
    ("schedule_events", "analytics-schedule"),
    ("schedule_events", "schedule-notifications"),
    ("schedule_events", "schedule-runtime-lifecycle"),
    ("schedule_events", "workflow-schedule-events"),
    ("surface_events", "agent-notification-settled"),
    ("surface_events", "agent-surfaces.onboarding"),
    ("surface_events", "analytics-surface"),
    ("surface_events", "surface-webhook-events"),
    ("workflow_run_events", "analytics-workflow"),
    ("workflow_run_events", "schedule-workflow-outcomes"),
)


async def destroy_group(redis: Redis, stream: str, group: str) -> bool:
    """Remove a group; False when it (or its stream) was already gone."""
    try:
        return bool(await redis.xgroup_destroy(stream, group))
    except ResponseError:
        return False


async def group_backlog(redis: Redis, stream: str, group: str) -> int | None:
    """Pending plus unread entries of ``group``; None when the group is absent."""
    try:
        groups = await redis.xinfo_groups(stream)
    except ResponseError:
        return None
    for info in groups:
        name = _text(_field(info, "name"))
        if name != group:
            continue
        pending = int(_field(info, "pending") or 0)
        lag = _field(info, "lag")
        if lag is None:
            # Redis < 7 has no lag; count what is past the last delivered id.
            last = _text(_field(info, "last-delivered-id")) or "0-0"
            lag = len(await redis.xrange(stream, min=f"({last}", max="+") or [])
        return pending + int(lag)
    return None


def _field(mapping: Any, name: str) -> Any:
    """A redis-py reply field, whichever of str or bytes its keys came back as."""
    if name in mapping:
        return mapping[name]
    return mapping.get(name.encode())


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return "" if value is None else str(value)
