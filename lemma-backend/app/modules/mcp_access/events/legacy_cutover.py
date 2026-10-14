"""The cutover's converter for MCP deliveries whose job only Redis held.

The old consumer fanned a record event out into one streaq job per matching
subscription (``deliver_mcp_event``), keyed by the subscription and the event.
A job still queued at the cutover becomes what the change-log subscriber
writes today: the ``mcp_event_deliveries`` row, unique per (subscription,
change) with the old event's id as the change key -- so the change, if it also
reaches the subscriber through the log, is delivered once -- and its
``mcp.deliver`` item. Worker only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.cutover.routes import legacy_converter
from app.core.infrastructure.work import JobContext, WorkWriter, pod_namespace
from app.modules.mcp_access.events.event_deliveries import DELIVER, DeliveryRef
from app.modules.mcp_access.infrastructure.models import (
    McpEventDelivery,
    McpEventSubscription,
)


@legacy_converter("streaq:deliver_mcp_event")
async def mcp_delivery(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    del route
    kwargs = dict(event.get("kwargs") or {})
    public_id = str(kwargs.get("subscription_id") or "")
    event_id = str(kwargs.get("event_id") or "")
    if not public_id or not event_id or not kwargs.get("pod_id"):
        return
    pod_id = UUID(str(kwargs["pod_id"]))
    occurred = kwargs.get("occurred_at")
    async with ctx.complete_work() as uow:
        row_id = await uow.session.scalar(
            select(McpEventSubscription.id).where(
                McpEventSubscription.public_id == public_id
            )
        )
        if row_id is None:
            return
        delivery_id = await uow.session.scalar(
            insert(McpEventDelivery)
            .values(
                subscription_id=row_id,
                change_key=event_id,
                pod_id=pod_id,
                table_name=str(kwargs.get("table") or ""),
                record_id=str(kwargs.get("record_id") or ""),
                occurred_at=(
                    datetime.fromisoformat(str(occurred).replace("Z", "+00:00"))
                    if occurred
                    else datetime.now(UTC)
                ),
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_mcp_event_deliveries_change")
            .returning(McpEventDelivery.id)
        )
        if delivery_id is None:
            return
        await WorkWriter.of(uow).add(
            DELIVER,
            pod_namespace(pod_id),
            DeliveryRef(delivery_id=delivery_id),
            dedup_key=f"mcp:{public_id}:{event_id}",
            rate_key=f"mcp:{public_id}",
        )
