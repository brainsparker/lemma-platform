"""A pod's channels, for its members.

Reading a channel takes what reading the pod's conversations takes: a channel
is where people talk with the pod, and its lines are as private as what was
said. A member sees their own direct channel and every group's. A contact's
channel takes seeing the pod's contacts too -- an editor's or an admin's, not
every member's (``access.visible``). Stepping in to answer a contact by hand
takes what writing to a contact takes.

A channel made in Lemma -- a GROUP no outside place feeds -- is any member's to
make. Whoever made it, or may change the pod, may rename, re-describe and
archive it, and add or remove its members; anybody may leave.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.modules.channels.api.access import (
    cursor,
    may_manage,
    sees_contacts,
    visible,
)
from app.modules.channels.api.message_schemas import message_people, preview
from app.modules.channels.api.schemas import (
    ChannelCreateRequest,
    ChannelDetailResponse,
    ChannelListResponse,
    ChannelResponse,
    ChannelUpdateRequest,
    NotifyRequest,
    ParticipantsAddRequest,
    channel_people,
    channel_response,
    detail_response,
)
from app.modules.channels.domain.entities import Channel, ChannelKind, Principal
from app.modules.channels.infrastructure.inbox import InboxReads
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.names import Names
from app.modules.channels.infrastructure.repository import (
    ChannelRepository,
)
from app.modules.channels.infrastructure.rows import MAX_LINES
from app.modules.pod.contracts.members import pod_member_id

router = APIRouter(prefix="/pods/{pod_id}/channels", tags=["Channels"])

_ALL_KINDS = (ChannelKind.DIRECT, ChannelKind.CONTACT, ChannelKind.GROUP)


@router.get(
    "",
    operation_id="channel.list",
    response_model=ChannelListResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def list_channels(
    pod_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    kind: ChannelKind | None = Query(default=None),
    archived: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=MAX_LINES),
    before: str | None = Query(default=None, max_length=100),
) -> ChannelListResponse:
    """The pod's channels this member sees, most recently active first, each
    with what is new in it for them and its last line."""
    kinds = (kind,) if kind else _ALL_KINDS
    if ChannelKind.CONTACT in kinds and not await sees_contacts(pod_id):
        kinds = tuple(each for each in kinds if each is not ChannelKind.CONTACT)
    if not kinds:
        return ChannelListResponse(items=[], next_before=None)
    repository = ChannelRepository(uow.session)
    channels = await repository.list_for_member(
        pod_id=pod_id,
        user_id=user.id,
        kinds=kinds,
        before=cursor(before),
        limit=limit,
        archived=archived,
    )
    last = channels[-1] if len(channels) == limit else None
    return ChannelListResponse(
        items=await _rows(uow, channels, pod_id=pod_id, user_id=user.id),
        next_before=(
            f"{(last.last_message_at or last.created_at).isoformat()}|{last.id}"
            if last
            else None
        ),
    )


async def _rows(
    uow: UoWDep, channels: list[Channel], *, pod_id: UUID, user_id: UUID
) -> list[ChannelResponse]:
    """Each channel with what is new in it for the reader, its places, and its
    last line -- in a few reads for the whole page."""
    ids = [channel.id for channel in channels]
    unread = await InboxReads(uow.session).unread(user_id=user_id, channel_ids=ids)
    sources = await ChannelRepository(uow.session).sources_of_many(ids)
    latest = await LineRepository(uow.session).latest_in(ids)
    people = [person for channel in channels for person in channel_people(channel)]
    people += [person for line in latest.values() for person in message_people(line)]
    names = await Names(uow).of(pod_id=pod_id, people=people)
    rows: list[ChannelResponse] = []
    for channel in channels:
        line = latest.get(channel.id)
        rows.append(
            channel_response(
                channel,
                names=names,
                unread=unread.get(channel.id),
                sources=sources.get(channel.id, []),
                last_line=preview(
                    line,
                    reader_id=user_id,
                    names=names,
                    in_their_direct=channel.kind is ChannelKind.DIRECT,
                )
                if line
                else None,
            )
        )
    return rows


@router.post(
    "",
    operation_id="channel.create",
    response_model=ChannelDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.CONVERSATION_WRITE)],
)
async def create_channel(
    pod_id: UUID, body: ChannelCreateRequest, user: CurrentUser, uow: UoWDep
) -> ChannelDetailResponse:
    """A channel made in Lemma, with its maker and the members they named in it."""
    repository = ChannelRepository(uow.session)
    channel = await repository.open_group(
        pod_id=pod_id,
        title=body.title.strip(),
        topic=(body.topic or "").strip() or None,
        created_by_user_id=user.id,
    )
    for member in dict.fromkeys([user.id, *body.members]):
        if await pod_member_id(uow, pod_id, member) is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only members of the pod can be added",
            )
        await repository.join(channel=channel, principal=Principal.member(member))
    await repository.mark_read(channel=channel, user_id=user.id)
    await uow.commit()
    return await _detail(uow, channel, user_id=user.id)


@router.get(
    "/{channel_id}",
    operation_id="channel.get",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def get_channel(
    pod_id: UUID, channel_id: UUID, user: CurrentUser, uow: UoWDep
) -> ChannelDetailResponse:
    """One channel, with the places it is fed from, the people in it, and how
    the reader hears of it."""
    channel = await visible(
        ChannelRepository(uow.session),
        pod_id=pod_id,
        channel_id=channel_id,
        user_id=user.id,
    )
    return await _detail(uow, channel, user_id=user.id)


async def _detail(
    uow: UoWDep, channel: Channel, *, user_id: UUID
) -> ChannelDetailResponse:
    repository = ChannelRepository(uow.session)
    participants = await repository.participants(channel.id)
    names = await Names(uow).of(
        pod_id=channel.pod_id,
        people=[*channel_people(channel), *(p.principal for p in participants)],
    )
    unread = await InboxReads(uow.session).unread(
        user_id=user_id, channel_ids=[channel.id]
    )
    return detail_response(
        channel,
        sources=await repository.sources_of(channel.id),
        participants=participants,
        names=names,
        unread=unread.get(channel.id),
        reader_id=user_id,
    )


async def _changeable(
    uow: UoWDep, pod_id: UUID, channel_id: UUID, user_id: UUID
) -> Channel:
    """A group this member may change: they made it, or may change the pod."""
    channel = await visible(
        ChannelRepository(uow.session),
        pod_id=pod_id,
        channel_id=channel_id,
        user_id=user_id,
    )
    if channel.kind is not ChannelKind.GROUP:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Only a group's name, topic and members can be changed",
        )
    if channel.created_by_user_id != user_id and not await may_manage(pod_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Only who made this channel, or an admin, can change it",
        )
    return channel


@router.patch(
    "/{channel_id}",
    operation_id="channel.update",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CONVERSATION_WRITE)],
)
async def update_channel(
    pod_id: UUID,
    channel_id: UUID,
    body: ChannelUpdateRequest,
    user: CurrentUser,
    uow: UoWDep,
) -> ChannelDetailResponse:
    """Rename, re-describe or archive a group."""
    channel = await _changeable(uow, pod_id, channel_id, user.id)
    changed = await ChannelRepository(uow.session).change(
        channel.id,
        title=body.title.strip() if body.title else None,
        topic=body.topic.strip() if body.topic is not None else None,
        archived=body.archived,
    )
    await uow.commit()
    return await _detail(uow, changed or channel, user_id=user.id)


@router.post(
    "/{channel_id}/participants",
    operation_id="channel.participant.add",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CONVERSATION_WRITE)],
)
async def add_participants(
    pod_id: UUID,
    channel_id: UUID,
    body: ParticipantsAddRequest,
    user: CurrentUser,
    uow: UoWDep,
) -> ChannelDetailResponse:
    """Add members of the pod to a group."""
    channel = await _changeable(uow, pod_id, channel_id, user.id)
    repository = ChannelRepository(uow.session)
    for member in dict.fromkeys(body.members):
        if await pod_member_id(uow, pod_id, member) is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only members of the pod can be added",
            )
        await repository.join(channel=channel, principal=Principal.member(member))
    await uow.commit()
    return await _detail(uow, channel, user_id=user.id)


@router.delete(
    "/{channel_id}/participants/{member_id}",
    operation_id="channel.participant.remove",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def remove_participant(
    pod_id: UUID, channel_id: UUID, member_id: UUID, user: CurrentUser, uow: UoWDep
) -> None:
    """Leave a channel, or -- for who may change it -- take somebody out."""
    if member_id == user.id:
        channel = await visible(
            ChannelRepository(uow.session),
            pod_id=pod_id,
            channel_id=channel_id,
            user_id=user.id,
        )
    else:
        channel = await _changeable(uow, pod_id, channel_id, user.id)
    await ChannelRepository(uow.session).leave(
        channel_id=channel.id, principal=Principal.member(member_id)
    )
    await uow.commit()


@router.put(
    "/{channel_id}/me",
    operation_id="channel.notify.update",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def set_my_notify(
    pod_id: UUID,
    channel_id: UUID,
    body: NotifyRequest,
    user: CurrentUser,
    uow: UoWDep,
) -> ChannelDetailResponse:
    """How the reader hears of this channel's lines outside the app."""
    repository = ChannelRepository(uow.session)
    channel = await visible(
        repository, pod_id=pod_id, channel_id=channel_id, user_id=user.id
    )
    await repository.set_notify(
        channel=channel,
        user_id=user.id,
        notify=body.notify,
        notify_via=(
            tuple(dict.fromkeys(via.strip().upper() for via in body.notify_via))
            if body.notify_via is not None
            else None
        ),
    )
    await uow.commit()
    return await _detail(uow, channel, user_id=user.id)


@router.post(
    "/{channel_id}/read",
    operation_id="channel.mark_read",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def mark_channel_read(
    pod_id: UUID, channel_id: UUID, user: CurrentUser, uow: UoWDep
) -> None:
    """Everything in the channel up to now has been read by this member, and
    every line naming them."""
    channels = ChannelRepository(uow.session)
    channel = await visible(
        channels, pod_id=pod_id, channel_id=channel_id, user_id=user.id
    )
    await channels.mark_read(channel=channel, user_id=user.id)
    await InboxReads(uow.session).read_mentions(channel_id=channel.id, user_id=user.id)
    await uow.commit()


@router.put(
    "/{channel_id}/handler",
    operation_id="channel.handler.take",
    response_model=ChannelResponse,
    dependencies=[require_action(Permissions.CONTACT_MESSAGE)],
)
async def take_channel(
    pod_id: UUID, channel_id: UUID, user: CurrentUser, uow: UoWDep
) -> ChannelResponse:
    """Answer a contact by hand: their agent stays quiet until it is handed back."""
    return await _set_handler(uow, pod_id, channel_id, user.id, handler=user.id)


@router.delete(
    "/{channel_id}/handler",
    operation_id="channel.handler.release",
    response_model=ChannelResponse,
    dependencies=[require_action(Permissions.CONTACT_MESSAGE)],
)
async def release_channel(
    pod_id: UUID, channel_id: UUID, user: CurrentUser, uow: UoWDep
) -> ChannelResponse:
    """Hand the contact back to the channel's agents."""
    return await _set_handler(uow, pod_id, channel_id, user.id, handler=None)


async def _set_handler(
    uow: UoWDep,
    pod_id: UUID,
    channel_id: UUID,
    user_id: UUID,
    *,
    handler: UUID | None,
) -> ChannelResponse:
    channels = ChannelRepository(uow.session)
    channel = await visible(
        channels, pod_id=pod_id, channel_id=channel_id, user_id=user_id
    )
    if channel.kind is not ChannelKind.CONTACT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Only a contact's channel is answered by hand",
        )
    await channels.set_handler(channel.id, handler)
    await uow.commit()
    updated = await channels.get(channel.id)
    target = updated or channel
    names = await Names(uow).of(pod_id=pod_id, people=channel_people(target))
    return channel_response(target, names=names)
