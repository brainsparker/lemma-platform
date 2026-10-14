"""The legacy Redis streams and consumer groups, read without FastStream.

Every group the old transport ever declared (``LEGACY_GROUPS``), and which of
them were *owed* work that exists nowhere else (``OWED_GROUPS``, design §13
rule 2): a webhook already answered 200, a record change, a schedule fire. The
rest moved to work seeded from domain state (rule 1) or are analytics, so
their backlog is redundant once their consumer is gone.

``decode_entry`` reads FastStream's envelope (binary ``BinaryMessageFormatV1``
or the older JSON ``{"data": ...}``) itself; ``read_group`` takes a converter's
next batch from a group (its own pending entries first, then new ones, then
what departed consumers left pending), so the group itself is the converter's
cursor and an ``XACK`` is its progress.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from struct import pack, unpack
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

#: The groups whose unfinished entries are work nothing else holds, and the
#: converter (work kind) that drains each. The gate needs each absent (the
#: converter destroys it when done) or with nothing pending or unread.
OWED_GROUPS: dict[tuple[str, str], str] = {
    ("datastore.events", "schedule-datastore-events"): "changes.drain",
    ("datastore.events", "mcp-access-event-deliveries"): "changes.drain",
    ("surface_events", "surface-webhook-events"): "surfaces.drain_events",
    ("surface_events", "agent-surfaces.onboarding"): "surfaces.drain_events",
    ("surface_events", "agent-notification-settled"): "surfaces.drain_events",
    ("schedule_events", "workflow-schedule-events"): "workflow.drain_schedule_fires",
}

#: The outbox event types those converters also take from unpublished rows.
OWED_OUTBOX_TYPES: tuple[str, ...] = (
    "surface.webhook.received",
    "surface.onboarding.ready",
    "notification.settled",
    "schedule.fired",
)

#: FastStream's binary envelope (``BinaryMessageFormatV1``).
_BINARY_MAGIC = b"\x89BIN\x0d\x0a\x1a\x0a"
_DATA_KEYS = (b"__data__", "__data__")


def encode_binary_entry(data: bytes, headers: Mapping[str, str] | None = None) -> bytes:
    """An entry body in FastStream's binary envelope, as the old publisher wrote it.

    For tests that plant a legacy backlog; ``decode_entry`` reads it back.
    """
    header_bytes = bytearray()
    items = list((headers or {}).items())
    for key, value in items:
        for text_value in (key, value):
            raw = text_value.encode()
            header_bytes += pack(">H", len(raw)) + raw
    headers_start = len(_BINARY_MAGIC) + 2 + 8
    data_start = headers_start + 2 + len(header_bytes)
    return (
        _BINARY_MAGIC
        + pack(">H", 1)
        + pack(">I", headers_start)
        + pack(">I", data_start)
        + pack(">H", len(items))
        + bytes(header_bytes)
        + data
    )


def decode_entry(fields: Mapping[Any, Any]) -> dict[str, Any] | None:
    """The event a stream entry carries, however FastStream wrapped it."""
    blob = next((fields[key] for key in _DATA_KEYS if key in fields), None)
    if blob is None:
        return None
    if isinstance(blob, str):
        blob = blob.encode()
    data: bytes = blob
    if blob.startswith(_BINARY_MAGIC):
        (data_start,) = unpack(">I", blob[14:18])
        data = blob[data_start:]
    try:
        decoded = json.loads(data)
    except ValueError:
        return None
    if isinstance(decoded, dict) and isinstance(decoded.get("data"), str):
        try:
            decoded = json.loads(decoded["data"])
        except ValueError:
            return None
    return decoded if isinstance(decoded, dict) else None


async def read_group(
    redis: Redis, stream: str, group: str, consumer: str, *, count: int
) -> list[tuple[bytes, Mapping[bytes, bytes]]]:
    """``consumer``'s next batch of ``group``: empty when the group is done or gone.

    Its own pending entries first (a pass that crashed before acking), then new
    ones, then entries departed consumers left pending. ``redis`` must not
    decode responses: the binary envelope is not UTF-8.
    """
    try:
        for start in ("0", ">"):
            reply = await redis.xreadgroup(
                group, consumer, {stream: start}, count=count
            )
            entries = [entry for _, batch in reply or [] for entry in batch]
            if entries:
                return entries
        claimed = await redis.xautoclaim(
            stream, group, consumer, min_idle_time=0, count=count
        )
    except ResponseError as exc:
        if "NOGROUP" in str(exc):
            return []
        raise
    return list(claimed[1]) if claimed else []


async def destroy_group(redis: Redis, stream: str, group: str) -> bool:
    """Remove a drained group; False when it (or its stream) was already gone."""
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
            lag = len(await redis.xrange(stream, min=f"({last}", max="+"))
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
