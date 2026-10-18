"""A channel's lines: reading the timeline and its threads, writing to it,
and answering what it asks -- plus each member's inbox, and search.

Writing in a channel takes what writing in the pod's conversations takes, and
in a contact's channel what writing to a contact takes, since it reaches them.
What a line asks is answered by the one person it waits on.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.api.dependencies import CurrentUser, UoWDep, get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.authorization.context import ResourceRef
from app.core.authorization.current import get_current_context
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.modules.channels.api.access import cursor, sees_contacts, visible
from app.modules.channels.api.message_schemas import (
    ChannelAnswerRequest,
    ChannelEditRequest,
    ChannelMessageResponse,
    ChannelMessagesResponse,
    ChannelPostRequest,
    InboxResponse,
    InboxTotals,
    SearchHit,
    SearchResponse,
    WaitingItem,
    message_people,
    message_response,
)
from app.modules.channels.api.schemas import channel_people, channel_response
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Principal,
    SenderKind,
)
from app.modules.channels.domain.projection import withheld_from
from app.modules.channels.infrastructure.inbox import InboxReads
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.names import Names
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.rows import MAX_LINES
from app.modules.channels.services.messages import (
    LineRefused,
    MemberLines,
    MemberPost,
    answer_question,
)

router = APIRouter(prefix="/pods/{pod_id}/channels", tags=["Channels"])
#: Routes with a fixed segment where a channel id would go; included first.
inbox_router = APIRouter(prefix="/pods/{pod_id}", tags=["Channels"])


def _refused(error: LineRefused) -> HTTPException:
    return HTTPException(error.status_code, detail=str(error))


async def _page(
    uow: UoWDep,
    channel: Channel,
    lines: list[ChannelMessage],
    *,
    reader_id: UUID,
    limit: int,
) -> ChannelMessagesResponse:
    names = await Names(uow).of(
        pod_id=channel.pod_id,
        people=[person for line in lines for person in message_people(line)],
    )
    first = lines[0] if len(lines) == limit else None
    return ChannelMessagesResponse(
        items=[
            message_response(
                line,
                reader_id=reader_id,
                names=names,
                in_their_direct=channel.kind is ChannelKind.DIRECT,
            )
            for line in lines
        ],
        next_before=f"{first.created_at.isoformat()}|{first.id}" if first else None,
    )


@router.get(
    "/{channel_id}/messages",
    operation_id="channel.message.list",
    response_model=ChannelMessagesResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def list_channel_messages(
    pod_id: UUID,
    channel_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    limit: int = Query(default=50, ge=1, le=MAX_LINES),
    before: str | None = Query(default=None, max_length=100),
) -> ChannelMessagesResponse:
    """The channel's timeline, oldest first: who said what, when and where.
    A thread's replies are not in it; its first line counts them."""
    channel = await visible(
        ChannelRepository(uow.session),
        pod_id=pod_id,
        channel_id=channel_id,
        user_id=user.id,
    )
    lines = await LineRepository(uow.session).recent(
        channel.id, limit=limit, before=cursor(before), top_level=True
    )
    return await _page(uow, channel, lines, reader_id=user.id, limit=limit)


@router.get(
    "/{channel_id}/messages/{message_id}/thread",
    operation_id="channel.message.thread",
    response_model=ChannelMessagesResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def list_thread(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    limit: int = Query(default=50, ge=1, le=MAX_LINES),
    before: str | None = Query(default=None, max_length=100),
) -> ChannelMessagesResponse:
    """A thread's replies, oldest first, the newest page of them."""
    channel = await visible(
        ChannelRepository(uow.session),
        pod_id=pod_id,
        channel_id=channel_id,
        user_id=user.id,
    )
    lines = LineRepository(uow.session)
    root = await lines.get(message_id)
    if root is None or root.channel_id != channel.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Line not found")
    replies = await lines.thread(
        root.thread_root_id or root.id, limit=limit, before=cursor(before)
    )
    return await _page(uow, channel, replies, reader_id=user.id, limit=limit)


async def _writable(
    uow: UoWDep, pod_id: UUID, channel_id: UUID, user_id: UUID
) -> Channel:
    channel = await visible(
        ChannelRepository(uow.session),
        pod_id=pod_id,
        channel_id=channel_id,
        user_id=user_id,
    )
    if channel.kind is ChannelKind.CONTACT:
        ctx = get_current_context()
        if ctx is None or not await ctx.can(
            Permissions.CONTACT_MESSAGE, ResourceRef.pod(pod_id)
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail="Writing to a contact is for the pod's editors and admins",
            )
    if channel.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This channel is archived")
    return channel


async def _one(
    uow: UoWDep, channel: Channel, line: ChannelMessage, user_id: UUID
) -> ChannelMessageResponse:
    names = await Names(uow).of(pod_id=channel.pod_id, people=message_people(line))
    return message_response(
        line,
        reader_id=user_id,
        names=names,
        in_their_direct=channel.kind is ChannelKind.DIRECT,
    )


@router.post(
    "/{channel_id}/messages",
    operation_id="channel.message.post",
    response_model=ChannelMessageResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.CONVERSATION_WRITE)],
)
async def post_channel_message(
    pod_id: UUID,
    channel_id: UUID,
    body: ChannelPostRequest,
    user: CurrentUser,
    uow: UoWDep,
) -> ChannelMessageResponse:
    """Write in the channel. It goes to one outside place -- the one named, or
    the one the channel last heard from -- unless it is a note to members."""
    channel = await _writable(uow, pod_id, channel_id, user.id)
    people = [
        Principal.member(m.id)
        if m.kind is SenderKind.MEMBER
        else Principal.contact(m.id)
        for m in body.mentions
        if m.kind is not SenderKind.AGENT
    ]
    agents = [m.id for m in body.mentions if m.kind is SenderKind.AGENT]
    poster = Principal.member(user.id)
    named = await Names(uow).of(pod_id=pod_id, people=[poster])
    try:
        line = await MemberLines(uow).post(
            channel,
            user_id=user.id,
            display_name=named.get(poster),
            post=MemberPost(
                text=body.text,
                members_only=body.members_only,
                source_id=body.source_id,
                thread_root_id=body.thread_root_id,
                reply_to_id=body.reply_to_id,
                parts=tuple(body.parts),
                mentions=people,
                mentioned_agents=agents,
            ),
        )
    except LineRefused as refused:
        raise _refused(refused) from refused
    await uow.commit()
    return await _one(uow, channel, line, user.id)


@router.patch(
    "/{channel_id}/messages/{message_id}",
    operation_id="channel.message.update",
    response_model=ChannelMessageResponse,
    dependencies=[require_action(Permissions.CONVERSATION_WRITE)],
)
async def edit_channel_message(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    body: ChannelEditRequest,
    user: CurrentUser,
    uow: UoWDep,
) -> ChannelMessageResponse:
    """Change what one's own line says. A copy already sent outside stays as sent."""
    channel = await _writable(uow, pod_id, channel_id, user.id)
    try:
        line = await MemberLines(uow).edit(
            channel, message_id, user_id=user.id, text=body.text
        )
    except LineRefused as refused:
        raise _refused(refused) from refused
    await uow.commit()
    return await _one(uow, channel, line, user.id)


@router.delete(
    "/{channel_id}/messages/{message_id}",
    operation_id="channel.message.delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CONVERSATION_WRITE)],
)
async def delete_channel_message(
    pod_id: UUID, channel_id: UUID, message_id: UUID, user: CurrentUser, uow: UoWDep
) -> None:
    """Delete one's own line: it stays in its place, saying nothing."""
    channel = await _writable(uow, pod_id, channel_id, user.id)
    try:
        await MemberLines(uow).delete(channel, message_id, user_id=user.id)
    except LineRefused as refused:
        raise _refused(refused) from refused
    await uow.commit()


@router.post(
    "/{channel_id}/messages/{message_id}/answer",
    operation_id="channel.message.answer",
    response_model=ChannelMessageResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def answer_channel_question(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    body: ChannelAnswerRequest,
    user: CurrentUser,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> ChannelMessageResponse:
    """Answer what this line asks the reader: a question, an approval, a form.

    No session is held across the answer: what asked the question takes it in
    its own -- a run resumed, a form submitted -- and may call out to do so.
    """
    async with uow_factory() as uow:
        channel = await visible(
            ChannelRepository(uow.session),
            pod_id=pod_id,
            channel_id=channel_id,
            user_id=user.id,
        )
    try:
        line = await answer_question(
            uow_factory,
            channel=channel,
            message_id=message_id,
            user_id=user.id,
            answer=body.answer,
        )
    except LineRefused as refused:
        raise _refused(refused) from refused
    async with uow_factory() as uow:
        return await _one(uow, channel, line, user.id)


@inbox_router.get(
    "/inbox",
    operation_id="channel.inbox.get",
    response_model=InboxResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def get_inbox(pod_id: UUID, user: CurrentUser, uow: UoWDep) -> InboxResponse:
    """What is new for the reader across the pod, and every question waiting
    on them, newest first."""
    totals = await InboxReads(uow.session).totals(user_id=user.id, pod_id=pod_id)
    waiting = await LineRepository(uow.session).waiting_on(
        user_id=user.id, pod_id=pod_id
    )
    channels = ChannelRepository(uow.session)
    found: dict[UUID, Channel] = {}
    for line in waiting:
        if line.channel_id not in found and (
            got := await channels.get(line.channel_id)
        ):
            found[line.channel_id] = got
    names = await Names(uow).of(
        pod_id=pod_id,
        people=[
            *(person for line in waiting for person in message_people(line)),
            *(
                person
                for channel in found.values()
                for person in channel_people(channel)
            ),
        ],
    )
    return InboxResponse(
        totals=InboxTotals(
            channels=totals.channels, mentions=totals.mentions, waiting=totals.waiting
        ),
        waiting=[
            WaitingItem(
                channel=channel_response(found[line.channel_id], names=names),
                message=message_response(
                    line,
                    reader_id=user.id,
                    names=names,
                    in_their_direct=found[line.channel_id].kind is ChannelKind.DIRECT,
                ),
            )
            for line in waiting
            if line.channel_id in found
        ],
    )


@inbox_router.get(
    "/channels/search",
    operation_id="channel.message.search",
    response_model=SearchResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def search_channels(
    pod_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
) -> SearchResponse:
    """Lines matching every word, newest first, in the channels the reader sees."""
    kinds = (ChannelKind.DIRECT, ChannelKind.GROUP) + (
        (ChannelKind.CONTACT,) if await sees_contacts(pod_id) else ()
    )
    channels = await ChannelRepository(uow.session).list_for_member(
        pod_id=pod_id, user_id=user.id, kinds=kinds, before=None, limit=MAX_LINES
    )
    kinds_of = {channel.id: channel.kind for channel in channels}
    found = await LineRepository(uow.session).search(
        channel_ids=list(kinds_of), words=q, limit=limit
    )
    names = await Names(uow).of(
        pod_id=pod_id,
        people=[person for line in found for person in message_people(line)],
    )
    return SearchResponse(
        items=[
            SearchHit(
                channel_id=line.channel_id,
                message=message_response(
                    line,
                    reader_id=user.id,
                    names=names,
                    in_their_direct=direct,
                ),
            )
            for line in found
            # A match in an answer withheld from the reader would say what it said.
            if not withheld_from(
                line,
                user_id=user.id,
                in_their_direct=(
                    direct := kinds_of.get(line.channel_id) is ChannelKind.DIRECT
                ),
            )
        ]
    )
