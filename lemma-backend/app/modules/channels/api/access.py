"""Who may do what with a pod's channels, for every channel route.

**Seeing** a channel is one rule (``domain.access.may_see``), asked once per
request through a ``Viewer`` built here: a member's own DIRECT channel; a
contact's channel for who holds ``contact.read``; a public group for every
member; a private group for its members. A channel the reader may not see
answers exactly as one that does not exist -- 404 -- since whether it exists is
not theirs to learn. Lists, counts, the inbox and search filter by the same
rule in SQL.

On top of seeing it:

=====================================  ========================================
Reading lines, the channel, members    ``conversation.read``
Writing, editing, deleting lines       ``conversation.write`` (+ ``contact.message``
                                       in a CONTACT channel)
Assignee, status, agent switch,        ``contact.message`` (CONTACT channels)
hand-back window, retrying a delivery
to a contact
Group: rename, topic, visibility,      the channel's owner, or ``pod.update``
archive, delete, members, roles,       (``may_manage``)
agents roster
Joining a public group or a CONTACT    ``conversation.read``
channel; leaving; one's own notify
Answering a question                   ``conversation.read``; only the person it
                                       waits on
=====================================  ========================================
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status

from app.core.api.dependencies import CurrentUser
from app.core.authorization.context import ResourceRef
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.access import Viewer, may_see
from app.modules.channels.domain.entities import Channel, MemberRole
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.repository import ChannelRepository


class Caller:
    """The member making this request: who they are to the visibility rule,
    and the pod-wide permissions channel routes ask about."""

    def __init__(
        self, viewer: Viewer, *, messages_contacts: bool, manages_pod: bool
    ) -> None:
        self.viewer = viewer
        self.messages_contacts = messages_contacts
        self.manages_pod = manages_pod

    @property
    def user_id(self) -> UUID:
        return self.viewer.user_id

    @property
    def pod_id(self) -> UUID:
        return self.viewer.pod_id


async def get_caller(pod_id: UUID, user: CurrentUser, ctx: PodContextDep) -> Caller:
    pod = ResourceRef.pod(pod_id)
    return Caller(
        Viewer(
            user_id=user.id,
            pod_id=pod_id,
            sees_contacts=await ctx.can(Permissions.CONTACT_READ, pod),
        ),
        messages_contacts=await ctx.can(Permissions.CONTACT_MESSAGE, pod),
        manages_pod=await ctx.can(Permissions.POD_UPDATE, pod),
    )


CallerDep = Annotated[Caller, Depends(get_caller)]


def missing(what: str = "Channel") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{what} not found")


def conflict(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail=detail)


def forbidden(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail=detail)


def unprocessable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


async def visible(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel_id: UUID
) -> Channel:
    """The channel, if it is in the pod and the caller may see it; else 404."""
    channel = await ChannelRepository(uow.session).in_pod(
        pod_id=caller.pod_id, channel_id=channel_id
    )
    if channel is None:
        raise missing()
    is_member = (
        await MemberRepository(uow.session).of_user(channel.id, caller.user_id)
        is not None
    )
    if not may_see(channel, caller.viewer, is_member=is_member):
        raise missing()
    return channel


async def may_manage(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel: Channel
) -> bool:
    """Whether the caller may change this channel's settings and roster: its
    owner, or whoever may change the pod."""
    if caller.manages_pod:
        return True
    seat = await MemberRepository(uow.session).of_user(channel.id, caller.user_id)
    return seat is not None and seat.role is MemberRole.OWNER


async def require_manage(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel: Channel
) -> None:
    if not await may_manage(uow, caller, channel):
        raise forbidden("Only this channel's owners, or an admin, can change it")


def require_contact_message(caller: Caller) -> None:
    if not caller.messages_contacts:
        raise forbidden("Writing to contacts is for the pod's editors and admins")


# -- cursors -----------------------------------------------------------------------


def encode_cursor(at: datetime, row_id: UUID) -> str:
    raw = f"{at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    """A cursor this API handed out, or a 400."""
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        at, _, row_id = raw.partition("|")
        return datetime.fromisoformat(at), UUID(row_id)
    except ValueError:  # bad base64, text or fields alike
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="That cursor is not one we gave"
        ) from None
