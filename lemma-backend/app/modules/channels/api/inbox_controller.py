"""Each member's inbox across a pod's channels, and search over them.

Both read only channels the member may see (the one visibility rule, in SQL):
a channel they cannot open is never counted, listed or found.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.modules.channels.api.access import (
    CallerDep,
    decode_cursor,
    encode_cursor,
    visible,
)
from app.modules.channels.api.message_schemas import (
    InboxItem,
    InboxResponse,
    InboxTotals,
    SearchResponse,
    hidden_from,
)
from app.modules.channels.api.views import lines_across
from app.modules.channels.infrastructure.inbox import UNREAD_CAP, InboxReads
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.questions import QuestionRepository
from app.modules.channels.infrastructure.repository import ChannelRepository

#: Routes with a fixed segment under the pod; included before the channels'.
router = APIRouter(prefix="/pods/{pod_id}", tags=["Channels"])

#: The most items each of the inbox's lists shows.
INBOX_ITEMS = 50


@router.get(
    "/inbox",
    operation_id="channel.inbox.get",
    response_model=InboxResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def get_inbox(pod_id: UUID, uow: UoWDep, caller: CallerDep) -> InboxResponse:
    """What is new for the reader across the pod: the badges, the questions
    waiting on them, and the unread lines naming them -- newest first."""
    inbox = InboxReads(uow.session)
    totals = await inbox.totals(caller.viewer)
    waiting = await QuestionRepository(uow.session).waiting_on(
        caller.viewer, limit=INBOX_ITEMS
    )
    mentioned = await inbox.mentioned_in(caller.viewer, limit=INBOX_ITEMS)
    lines = await LineRepository(uow.session).by_ids(
        [question.message_id for question in waiting]
        + [message_id for _, message_id in mentioned]
    )
    channels = await ChannelRepository(uow.session).by_ids(
        {line.channel_id for line in lines.values()}
    )

    async def items(ids: list[UUID]) -> list[InboxItem]:
        found = [
            (channels[line.channel_id], line)
            for line in (lines.get(each) for each in ids)
            if line is not None and line.channel_id in channels
        ]
        return [
            InboxItem(channel=channel, message=message)
            for channel, message in await lines_across(uow, caller, found)
        ]

    return InboxResponse(
        totals=InboxTotals(
            channels=totals.channels, mentions=totals.mentions, waiting=totals.waiting
        ),
        waiting=await items([question.message_id for question in waiting]),
        mentions=await items([message_id for _, message_id in mentioned]),
    )


@router.get(
    "/search",
    operation_id="channel.search",
    response_model=SearchResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def search(
    pod_id: UUID,
    uow: UoWDep,
    caller: CallerDep,
    q: str = Query(min_length=1, max_length=200),
    channel_id: UUID | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=30, ge=1, le=UNREAD_CAP),
) -> SearchResponse:
    """Lines matching every word, newest first, in the channels the reader
    sees -- or in one of them. An answer withheld from the reader is not found
    by what it said."""
    if channel_id is not None:
        await visible(uow, caller, channel_id)
    found = await LineRepository(uow.session).search(
        caller.viewer,
        query=q,
        limit=limit,
        channel_id=channel_id,
        before=decode_cursor(cursor),
    )
    channels = await ChannelRepository(uow.session).by_ids(
        {line.channel_id for line in found}
    )
    shown = [
        (channels[line.channel_id], line)
        for line in found
        if line.channel_id in channels
        and not hidden_from(line, channels[line.channel_id], caller.user_id)
    ]
    last = found[-1] if len(found) == limit else None
    return SearchResponse(
        items=[
            InboxItem(channel=channel, message=message)
            for channel, message in await lines_across(uow, caller, shown)
        ],
        next_cursor=encode_cursor(last.created_at, last.id) if last else None,
    )
