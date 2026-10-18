"""Where a line was sent out to, and what became of it.

A line written in Lemma for somebody outside it -- an agent's message to a
member, a member's post to a contact -- is delivered by the module that owns
the platforms; it records each attempt here, and the app shows it on the line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select, update

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.infrastructure.models import ChannelMessageDeliveryModel
from app.modules.channels.infrastructure.rows import MAX_LINES, now


class DeliveryStatus(StrEnum):
    SENT = "SENT"
    FAILED = "FAILED"
    #: Nothing could carry it: it is in the app only.
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class Delivery:
    message_id: UUID
    platform: str
    status: DeliveryStatus
    error: str | None
    source_id: UUID | None
    delivered_at: datetime | None


async def record_delivery(
    uow: SqlAlchemyUnitOfWork,
    *,
    message_id: UUID,
    pod_id: UUID,
    platform: str,
    status: DeliveryStatus,
    recipient_user_id: UUID | None = None,
    recipient_contact_id: UUID | None = None,
    source_id: UUID | None = None,
    error: str | None = None,
    external_message_id: str | None = None,
) -> None:
    uow.session.add(
        ChannelMessageDeliveryModel(
            message_id=message_id,
            pod_id=pod_id,
            source_id=source_id,
            platform=platform[:20],
            recipient_user_id=recipient_user_id,
            recipient_contact_id=recipient_contact_id,
            status=status.value,
            error=error[:2000] if error else None,
            external_message_id=external_message_id[:255]
            if external_message_id
            else None,
            delivered_at=now() if status is DeliveryStatus.SENT else None,
        )
    )
    await uow.session.flush()


async def mark_delivery_failed(
    uow: SqlAlchemyUnitOfWork, *, message_id: UUID, platform: str, error: str
) -> bool:
    """A platform reported, after the fact, that a line it took never arrived.

    Whether there was a sent delivery of it there to mark.
    """
    result = await uow.session.execute(
        update(ChannelMessageDeliveryModel)
        .where(
            ChannelMessageDeliveryModel.message_id == message_id,
            ChannelMessageDeliveryModel.platform == platform,
            ChannelMessageDeliveryModel.status == DeliveryStatus.SENT.value,
        )
        .values(status=DeliveryStatus.FAILED.value, error=error[:2000])
    )
    return bool(getattr(result, "rowcount", 0))


async def deliveries_of(
    uow: SqlAlchemyUnitOfWork, message_ids: Sequence[UUID]
) -> dict[UUID, list[Delivery]]:
    """Each line's deliveries, oldest first."""
    if not message_ids:
        return {}
    rows = await uow.session.scalars(
        select(ChannelMessageDeliveryModel)
        .where(
            ChannelMessageDeliveryModel.message_id.in_(list(message_ids)[:MAX_LINES])
        )
        .order_by(ChannelMessageDeliveryModel.created_at)
        .limit(MAX_LINES * 2)
    )
    found: dict[UUID, list[Delivery]] = {}
    for row in rows:
        found.setdefault(row.message_id, []).append(
            Delivery(
                message_id=row.message_id,
                platform=row.platform,
                status=DeliveryStatus(row.status),
                error=row.error,
                source_id=row.source_id,
                delivered_at=row.delivered_at,
            )
        )
    return found


__all__ = [
    "Delivery",
    "DeliveryStatus",
    "deliveries_of",
    "mark_delivery_failed",
    "record_delivery",
]
