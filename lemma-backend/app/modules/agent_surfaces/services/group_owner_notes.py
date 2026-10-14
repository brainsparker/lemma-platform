"""Telling a member that somebody else changed the group they answer for."""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
)
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (  # noqa: E501
    NotificationRepository,
)


async def tell_previous_owner(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    previous_owner: UUID,
    actor_id: UUID,
    group: SurfaceGroup,
    what_changed: str,
) -> None:
    """Put a note in the inbox of the member whose group somebody else changed.

    Inbox only: it is news, not a question, and it is written on the request's
    own unit of work with nothing sent anywhere.
    """
    people = SqlAlchemySurfaceRoutingResolutionAdapter(uow)
    member_id = await people.get_pod_member_id(previous_owner, pod_id)
    if member_id is None:
        return
    actor = await people.get_user_display_name(actor_id) or "An admin of the space"
    place = group.title or "one of the space's groups"
    await NotificationRepository(uow).create(
        NotificationEntity(
            pod_id=pod_id,
            recipient_user_id=previous_owner,
            recipient_pod_member_id=member_id,
            actor_user_id=actor_id,
            origin_kind=NotificationOriginKind.API,
            title=f"{actor} changed {place}"[:120],
            body=f"{actor} {what_changed} in {place}.",
            expects_response=False,
            delivery_status=NotificationDeliveryStatus.UNDELIVERABLE,
        )
    )
