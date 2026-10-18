"""Which channels a member may read, for every channel route.

Another member's direct channel answers as missing, exactly as a channel that
does not exist: whether it does is not this member's to learn. So does a
contact's, to a member who may not see the pod's contacts (``contact.read``:
editors and admins).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status

from app.core.authorization.context import ResourceRef
from app.core.authorization.current import get_current_context
from app.core.authorization.permissions import Permissions
from app.modules.channels.domain.entities import Channel, ChannelKind
from app.modules.channels.infrastructure.repository import ChannelRepository


async def sees_contacts(pod_id: UUID) -> bool:
    """Whether this member may see the pod's contacts, and so their channels."""
    ctx = get_current_context()
    return ctx is not None and await ctx.can(
        Permissions.CONTACT_READ, ResourceRef.pod(pod_id)
    )


async def may_message_contacts(pod_id: UUID) -> bool:
    """Whether this member may write to the pod's contacts, and so decide who
    answers them."""
    ctx = get_current_context()
    return ctx is not None and await ctx.can(
        Permissions.CONTACT_MESSAGE, ResourceRef.pod(pod_id)
    )


async def may_manage(pod_id: UUID) -> bool:
    """Whether this member may change any channel of the pod, not only theirs."""
    ctx = get_current_context()
    return ctx is not None and await ctx.can(
        Permissions.POD_UPDATE, ResourceRef.pod(pod_id)
    )


async def visible(
    channels: ChannelRepository, *, pod_id: UUID, channel_id: UUID, user_id: UUID
) -> Channel:
    """The channel, if the pod has it and this member may read it."""
    channel = await channels.get(channel_id)
    if (
        channel is None
        or channel.pod_id != pod_id
        or (channel.kind is ChannelKind.DIRECT and channel.member_user_id != user_id)
        or (channel.kind is ChannelKind.CONTACT and not await sees_contacts(pod_id))
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return channel


def cursor(before: str | None) -> tuple[datetime, UUID] | None:
    """A ``time|id`` cursor this API handed out, or a refusal."""
    if before is None:
        return None
    at, _, row_id = before.partition("|")
    try:
        return datetime.fromisoformat(at), UUID(row_id)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="That cursor is not one we gave"
        ) from None
