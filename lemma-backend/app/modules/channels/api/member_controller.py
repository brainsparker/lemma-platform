"""Who is in a channel -- people and agents -- and how far each member read.

A member is addressed as ``{kind}:{id}``: ``MEMBER:<user id>``,
``CONTACT:<contact id>`` or ``AGENT:<agent id>``; the caller is ``me``.
Changing a group's roster is its owners' or an editor's (``channel.manage``);
anybody may leave a channel they are in, and join a public group (or, holding
``contact.read``, a contact's channel) to hear of it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.channels.api.access import (
    Caller,
    CallerDep,
    conflict,
    decode_cursor,
    encode_cursor,
    missing,
    require_manage,
    unprocessable,
    visible,
)
from app.modules.channels.api.controller import seat_agents, seat_people
from app.modules.channels.api.schemas import (
    ChannelDetailResponse,
    ChannelMemberListResponse,
    MembersAddRequest,
    MemberUpdateRequest,
    MyMembershipUpdate,
    ReadRequest,
    ReadResponse,
)
from app.modules.channels.api.views import channel_detail, members_response
from app.modules.channels.domain.access import may_see
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    MemberRole,
    Principal,
    SeatedBy,
    SenderKind,
)
from app.modules.channels.infrastructure.members import MAX_MEMBERS, MemberRepository
from app.modules.channels.infrastructure.repository import ChannelRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/pods/{pod_id}/channels", tags=["Channels"])


@router.post(
    "/{channel_id}/members",
    operation_id="channel.member.add",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def add_members(
    pod_id: UUID,
    channel_id: UUID,
    body: MembersAddRequest,
    uow: UoWDep,
    caller: CallerDep,
) -> ChannelDetailResponse:
    """Add members of the pod, and the pod's agents, to a group. An agent
    already in it answers as now asked."""
    channel = await _group(uow, caller, channel_id)
    await require_manage(uow, caller, channel)
    await seat_people(uow, channel, body.people, added_by=caller.user_id)
    await seat_agents(
        uow,
        channel,
        [(agent.id, agent.answers) for agent in body.agents],
        added_by=caller.user_id,
    )
    await uow.commit()
    return await channel_detail(uow, caller, channel)


@router.get(
    "/{channel_id}/members",
    operation_id="channel.member.list",
    response_model=ChannelMemberListResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def list_members(
    pod_id: UUID,
    channel_id: UUID,
    uow: UoWDep,
    caller: CallerDep,
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=MAX_MEMBERS),
) -> ChannelMemberListResponse:
    """Who is in a channel -- members, contacts and agents -- in the order
    they joined, a page at a time."""
    channel = await visible(uow, caller, channel_id)
    members = await MemberRepository(uow.session).page(
        channel.id, limit=limit, after=decode_cursor(cursor)
    )
    last = members[-1] if len(members) == limit else None
    return ChannelMemberListResponse(
        items=await members_response(uow, caller, members),
        next_cursor=(
            encode_cursor(last.joined_at, last.seat_id)
            if last is not None
            and last.joined_at is not None
            and last.seat_id is not None
            else None
        ),
    )


async def _group(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel_id: UUID
) -> Channel:
    channel = await visible(uow, caller, channel_id)
    if channel.kind is not ChannelKind.GROUP:
        raise conflict("Only a group's members can be changed")
    return channel


# -- the caller's own membership (before ``{kind}:{id}``) ---------------------------


@router.post(
    "/{channel_id}/members/me",
    operation_id="channel.member.join",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def join_channel(
    pod_id: UUID, channel_id: UUID, uow: UoWDep, caller: CallerDep
) -> ChannelDetailResponse:
    """Join a channel the reader may see: a public group, or -- holding
    `contact.read` -- a contact's channel. Holding `channel.manage`, a private
    group too, on request: how a pod admin finds and takes over a group nobody
    let them into; it is logged. Joining one already joined is nothing."""
    channel, by_right = await _joinable(uow, caller, channel_id)
    if channel.kind is ChannelKind.DIRECT:
        raise conflict("A member's own channel is theirs alone")
    await MemberRepository(uow.session).add_person(
        channel, Principal.member(caller.user_id), seated_by=SeatedBy.JOINED
    )
    await uow.commit()
    if by_right:
        logger.info(
            "channels.member.joined_private_by_right.observed",
            pod_id=str(channel.pod_id),
            channel_id=str(channel.id),
            user_id=str(caller.user_id),
        )
    return await channel_detail(uow, caller, channel)


async def _joinable(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel_id: UUID
) -> tuple[Channel, bool]:
    """The channel the caller asks to join, and whether only their
    ``channel.manage`` lets them (a private group they were not let into).
    Anybody else is answered as if it did not exist."""
    channel = await ChannelRepository(uow.session).in_pod(
        pod_id=caller.pod_id, channel_id=channel_id
    )
    if channel is None:
        raise missing()
    is_member = (
        await MemberRepository(uow.session).of_user(channel.id, caller.user_id)
        is not None
    )
    if may_see(channel, caller.viewer, is_member=is_member):
        return channel, False
    if caller.manages_channels and channel.kind is ChannelKind.GROUP:
        return channel, True
    raise missing()


@router.delete(
    "/{channel_id}/members/me",
    operation_id="channel.member.leave",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def leave_channel(
    pod_id: UUID, channel_id: UUID, uow: UoWDep, caller: CallerDep
) -> None:
    """Leave a channel. Leaving one not joined is nothing; a member's own
    channel cannot be left."""
    channel = await visible(uow, caller, channel_id)
    if channel.kind is ChannelKind.DIRECT:
        raise conflict("A member's own channel is theirs alone")
    await MemberRepository(uow.session).remove(channel.id, user_id=caller.user_id)
    await uow.commit()


@router.patch(
    "/{channel_id}/members/me",
    operation_id="channel.member.me_update",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def update_my_membership(
    pod_id: UUID,
    channel_id: UUID,
    body: MyMembershipUpdate,
    uow: UoWDep,
    caller: CallerDep,
) -> ChannelDetailResponse:
    """How the reader hears of this channel's lines outside the app. Absent is
    unchanged; `null` clears `notify_via` (the pod's rule) and `muted_until`."""
    channel = await visible(uow, caller, channel_id)
    named = set(body.model_fields_set)
    if "notify" in named and body.notify is None:
        raise unprocessable("Cannot clear notify: set ALL, MENTIONS or NONE")
    values: dict[str, object] = {}
    if "notify" in named:
        assert body.notify is not None
        values["notify"] = body.notify.value
    if "notify_via" in named:
        values["notify_via"] = (
            list(dict.fromkeys(body.notify_via))
            if body.notify_via is not None
            else None
        )
    if "muted_until" in named:
        values["muted_until"] = body.muted_until
    if not await MemberRepository(uow.session).set_prefs(
        channel.id, caller.user_id, values
    ):
        raise conflict("Join this channel first")
    await uow.commit()
    return await channel_detail(uow, caller, channel)


@router.post(
    "/{channel_id}/read",
    operation_id="channel.read",
    response_model=ReadResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def mark_read(
    pod_id: UUID, channel_id: UUID, body: ReadRequest, uow: UoWDep, caller: CallerDep
) -> ReadResponse:
    """The reader has seen the channel up to line `seq`: it never goes back,
    and never past the newest line. Mentions up to it are read too."""
    channel = await visible(uow, caller, channel_id)
    read_seq = await MemberRepository(uow.session).mark_read(
        channel_id=channel.id, user_id=caller.user_id, seq=body.seq
    )
    await uow.commit()
    return ReadResponse(read_seq=read_seq)


# -- somebody's membership -------------------------------------------------------------


@router.patch(
    "/{channel_id}/members/{kind}:{member_id}",
    operation_id="channel.member.update",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CHANNEL_WRITE)],
)
async def update_member(
    pod_id: UUID,
    channel_id: UUID,
    kind: SenderKind,
    member_id: UUID,
    body: MemberUpdateRequest,
    uow: UoWDep,
    caller: CallerDep,
) -> ChannelDetailResponse:
    """A person's role (`owner` or `member`), or when an agent answers."""
    channel = await _group(uow, caller, channel_id)
    await require_manage(uow, caller, channel)
    members = MemberRepository(uow.session)
    if kind is SenderKind.AGENT:
        if body.role is not None or body.answers is None:
            raise unprocessable("An agent takes `answers`, and only that")
        if not any(
            seat.agent_id == member_id for seat in await members.agents(channel.id)
        ):
            raise missing("Member")
        await members.add_agent(
            channel, member_id, answers=body.answers, added_by_user_id=caller.user_id
        )
    elif kind is SenderKind.MEMBER:
        role = body.role
        if body.answers is not None or role is None or role is MemberRole.GUEST:
            raise unprocessable("A member takes `role`: owner or member")
        if await members.of_user(channel.id, member_id) is None:
            raise missing("Member")
        await members.set_role(channel.id, member_id, role)
    else:
        raise conflict("A contact's place in a channel has nothing to change")
    await uow.commit()
    return await channel_detail(uow, caller, channel)


@router.delete(
    "/{channel_id}/members/{kind}:{member_id}",
    operation_id="channel.member.remove",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def remove_member(
    pod_id: UUID,
    channel_id: UUID,
    kind: SenderKind,
    member_id: UUID,
    uow: UoWDep,
    caller: CallerDep,
) -> None:
    """Take somebody out of a group -- its owners' or an editor's -- or leave a
    channel oneself."""
    channel = await visible(uow, caller, channel_id)
    leaving = kind is SenderKind.MEMBER and member_id == caller.user_id
    if channel.kind is ChannelKind.DIRECT:
        raise conflict("A member's own channel is theirs alone")
    if not leaving:
        if channel.kind is not ChannelKind.GROUP:
            raise conflict("Only a group's members can be changed")
        await require_manage(uow, caller, channel)
    removed = await MemberRepository(uow.session).remove(
        channel.id,
        user_id=member_id if kind is SenderKind.MEMBER else None,
        contact_id=member_id if kind is SenderKind.CONTACT else None,
        agent_id=member_id if kind is SenderKind.AGENT else None,
    )
    if not removed:
        raise missing("Member")
    await uow.commit()
