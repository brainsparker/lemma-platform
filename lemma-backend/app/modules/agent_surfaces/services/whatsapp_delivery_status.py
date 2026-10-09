"""Correcting a WhatsApp notification that Meta reported it never delivered.

Cloud API answers a send with 200 and reports most failures afterwards, in a
``statuses`` webhook (``platforms/whatsapp/sent_messages``). Those deliveries
carry no message, so they were parsed to nothing and dropped -- and a
notification sent to somebody outside the 24-hour window stayed recorded as
delivered while it never arrived.

Applied beside ``group_updates`` on every webhook, before the message paths and
never instead of them.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.ingress_request import SurfaceIngressRequest
from app.modules.agent_surfaces.domain.notification import NotificationDeliveryStatus
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (  # noqa: E501
    NotificationRepository,
)
from app.modules.agent_surfaces.platforms.whatsapp.parser import (
    WhatsAppFailedDelivery,
    failed_deliveries,
)
from app.modules.agent_surfaces.platforms.whatsapp.sent_messages import (
    notification_sent_as,
)

logger = get_logger(__name__)

#: Meta's codes for the two failures a person can do something about.
_OUTSIDE_REPLY_WINDOW = "131047"
_CANNOT_RECEIVE = "131026"


def failure_reason(failure: WhatsAppFailedDelivery) -> str:
    """What the notification's sender is told, in place of "delivered"."""
    if failure.code == _OUTSIDE_REPLY_WINDOW:
        return (
            "WhatsApp did not deliver it: more than 24 hours have passed since "
            "they last wrote, and only pre-approved template messages are allowed "
            "after that. Ask them to message the bot, or reach them another way."
        )
    if failure.code == _CANNOT_RECEIVE:
        return (
            "WhatsApp did not deliver it: this number cannot receive messages "
            "from the business right now."
        )
    detail = f" ({failure.reason})" if failure.reason else ""
    return f"WhatsApp reported the message as not delivered{detail}."


async def apply_delivery_statuses(
    uow: SqlAlchemyUnitOfWork,
    request: SurfaceIngressRequest,
    *,
    notifications: NotificationRepository | None = None,
) -> None:
    """Mark the notifications a ``statuses`` webhook says failed."""
    if request.payload.get("object") != "whatsapp_business_account":
        return
    failures = failed_deliveries(request.payload)
    if not failures:
        return
    notifications = notifications or NotificationRepository(uow)
    for failure in failures:
        try:
            async with connection_released(uow.session):  # Redis, not the DB
                notification_id = await notification_sent_as(failure.message_id)
        except RedisError, OSError:
            logger.warning(
                "agent_surfaces.whatsapp.sent_message_unreadable.degraded",
                exc_info=True,
            )
            notification_id = None
        logger.warning(
            "agent_surfaces.whatsapp.delivery_failed.degraded",
            error_code=failure.code,
            reason=failure.reason,
            notification_id=notification_id,
        )
        if notification_id is None:
            continue
        notification = await notifications.get(UUID(notification_id))
        if (
            notification is None
            or notification.delivery_status is not NotificationDeliveryStatus.DELIVERED
        ):
            continue
        notification.mark_delivery_failed(failure_reason(failure))
        await notifications.update(notification)
