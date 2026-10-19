"""Deliveries: what whoever owns the platforms reads and records as it sends.

Channels plans a delivery for every send outside Lemma and announces it
(``ChannelDeliveryDue``). The sender claims it, sends, and records the outcome
here; a claimed delivery that was sent is never sent again.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import Delivery
from app.modules.channels.infrastructure.deliveries import DeliveryRepository

__all__ = [
    "claim_delivery",
    "deliveries_of",
    "delivery_by_external",
    "delivery_by_id",
    "delivery_failed",
    "delivery_sent",
    "delivery_skipped",
    "delivery_undelivered",
    "due_deliveries",
    "retry_delivery",
]


async def delivery_by_id(
    uow: SqlAlchemyUnitOfWork, delivery_id: UUID
) -> Delivery | None:
    return await DeliveryRepository(uow.session).get(delivery_id)


async def claim_delivery(
    uow: SqlAlchemyUnitOfWork, delivery_id: UUID, *, at: datetime
) -> Delivery | None:
    """Take a due delivery to send it; ``None`` when it is not due -- sent,
    skipped, given up, or being sent by somebody else."""
    return await DeliveryRepository(uow.session).claim(delivery_id, at=at)


async def delivery_sent(
    uow: SqlAlchemyUnitOfWork,
    delivery_id: UUID,
    *,
    platform: str,
    external_message_id: str | None,
) -> None:
    await DeliveryRepository(uow.session).sent(
        delivery_id, platform=platform, external_message_id=external_message_id
    )


async def delivery_skipped(
    uow: SqlAlchemyUnitOfWork,
    delivery_id: UUID,
    *,
    reason: str,
    platform: str | None = None,
) -> None:
    """Not sent, on purpose: the reason a member reads."""
    await DeliveryRepository(uow.session).skipped(
        delivery_id, reason=reason, platform=platform
    )


async def delivery_failed(
    uow: SqlAlchemyUnitOfWork,
    delivery: Delivery,
    *,
    reason: str,
    platform: str | None = None,
) -> Delivery:
    """It did not go: due again after backoff, or given up after the last try."""
    return await DeliveryRepository(uow.session).failed(
        delivery, reason=reason, platform=platform
    )


async def retry_delivery(
    uow: SqlAlchemyUnitOfWork, delivery_id: UUID
) -> Delivery | None:
    return await DeliveryRepository(uow.session).retry(delivery_id)


async def delivery_undelivered(
    uow: SqlAlchemyUnitOfWork, *, message_id: UUID, platform: str, reason: str
) -> int:
    """A line the platform took and later reported it never delivered; how
    many of its deliveries that was."""
    return await DeliveryRepository(uow.session).undelivered(
        message_id=message_id, platform=platform, reason=reason
    )


async def delivery_by_external(
    uow: SqlAlchemyUnitOfWork, *, platform: str, external_message_id: str
) -> Delivery | None:
    """The delivery a platform's status report names."""
    return await DeliveryRepository(uow.session).by_external(
        platform=platform, external_message_id=external_message_id
    )


async def deliveries_of(
    uow: SqlAlchemyUnitOfWork, message_ids: Sequence[UUID]
) -> list[Delivery]:
    return await DeliveryRepository(uow.session).of_messages(message_ids)


async def due_deliveries(
    uow: SqlAlchemyUnitOfWork, *, at: datetime, limit: int = 100
) -> list[UUID]:
    return await DeliveryRepository(uow.session).due(at=at, limit=limit)
