"""A pod's channels: listed, made, read, changed, deleted.

Permissions are tabled in ``api.access``. A GET never writes: a member's own
channel with the pod is made by ``POST /channels/me`` (or by whatever first
writes to them there), and until then ``GET /channels/me`` is 404 and the list
does not show it.
"""

from __future__ import annotations

from uuid import UUID, uuid5

from fastapi import APIRouter, Header, Query, Response, status

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.api.access import (
    Caller,
    CallerDep,
    conflict,
    decode_cursor,
    encode_cursor,
    missing,
    require_contact_message,
    require_manage,
    unprocessable,
    visible,
)
from app.modules.channels.api.schemas import (
    CONTACT_FIELDS,
    GROUP_FIELDS,
    NOT_CLEARABLE,
    ChannelCreateRequest,
    ChannelDetailResponse,
    ChannelListResponse,
    ChannelUpdateRequest,
)
from app.modules.channels.api.views import channel_detail, channel_rows
from app.modules.channels.domain.entities import (
    AgentAnswers,
    Channel,
    ChannelKind,
    ContactStatus,
    Principal,
    Visibility,
)
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.names import Names
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.rows import now
from app.modules.channels.infrastructure.source_repository import SourceRepository
from app.modules.pod.contracts.members import pod_member_holds, pod_member_id

router = APIRouter(prefix="/pods/{pod_id}/channels", tags=["Channels"])

#: Namespace for channel ids derived from an ``Idempotency-Key``. Fixed for
#: good: a change would let a retry spanning a deploy make a second channel.
_IDEMPOTENT_CHANNELS = UUID("5f0e9a52-3c4b-4d7e-9b1a-8c2d6e4f1a37")

IdempotencyKey = Header(
    default=None, alias="Idempotency-Key", min_length=1, max_length=255
)


@router.get(
    "",
    operation_id="channel.list",
    response_model=ChannelListResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def list_channels(
    pod_id: UUID,
    uow: UoWDep,
    caller: CallerDep,
    kind: ChannelKind | None = Query(default=None),
    archived: bool = Query(default=False),
    contact_id: UUID | None = Query(
        default=None, description="Only this contact's channel."
    ),
    assignee: str | None = Query(
        default=None,
        max_length=36,
        description="`me`, or a member's user id: contacts' channels assigned to them.",
    ),
    unassigned: bool = Query(default=False),
    status_: ContactStatus | None = Query(default=None, alias="status"),
    joined: bool = Query(default=False, description="Only channels the reader is in."),
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
) -> ChannelListResponse:
    """The channels the reader may see, most recently active first, each with
    what is new in it for them and its last line."""
    channels = await ChannelRepository(uow.session).list_for(
        caller.viewer,
        kind=kind,
        archived=archived,
        contact_id=contact_id,
        assignee_user_id=_assignee(assignee, caller),
        unassigned=unassigned,
        status=status_,
        joined_only=joined,
        limit=limit,
        before=decode_cursor(cursor),
    )
    last = channels[-1] if len(channels) == limit else None
    return ChannelListResponse(
        items=await channel_rows(uow, caller, channels),
        next_cursor=encode_cursor(last.last_line_at, last.id) if last else None,
    )


def _assignee(value: str | None, caller: Caller) -> UUID | None:
    if value is None:
        return None
    if value == "me":
        return caller.user_id
    try:
        return UUID(value)
    except ValueError:
        raise unprocessable("`assignee` is `me` or a user id") from None


@router.post(
    "",
    operation_id="channel.create",
    response_model=ChannelDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.CONVERSATION_WRITE)],
)
async def create_channel(
    pod_id: UUID,
    body: ChannelCreateRequest,
    uow: UoWDep,
    caller: CallerDep,
    idempotency_key: str | None = IdempotencyKey,
) -> ChannelDetailResponse:
    """A group made in Lemma: public unless asked otherwise, with its maker as
    its owner and the members and agents named in it. The same
    `Idempotency-Key` from the same member returns the group it made."""
    channels = ChannelRepository(uow.session)
    title, topic = body.title, body.topic or None
    if idempotency_key is None:
        channel, made = (
            await channels.open_group(
                pod_id=caller.pod_id,
                title=title,
                topic=topic,
                visibility=body.visibility,
                created_by_user_id=caller.user_id,
            ),
            True,
        )
    else:
        channel, made = await channels.open_group_once(
            channel_id=uuid5(
                _IDEMPOTENT_CHANNELS,
                f"{caller.pod_id}:{caller.user_id}:{idempotency_key}",
            ),
            pod_id=caller.pod_id,
            title=title,
            topic=topic,
            visibility=body.visibility,
            created_by_user_id=caller.user_id,
        )
        if channel.pod_id != caller.pod_id:
            raise conflict("That Idempotency-Key was used elsewhere")
    if made:
        await seat_people(uow, channel, body.members, added_by=caller.user_id)
        await seat_agents(
            uow,
            channel,
            [(agent.id, agent.answers) for agent in body.agents],
            added_by=caller.user_id,
        )
    await uow.commit()
    return await channel_detail(uow, caller, channel)


async def seat_people(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    user_ids: list[UUID],
    *,
    added_by: UUID,
) -> None:
    """Put members of the pod in a channel; anybody else is refused."""
    members = MemberRepository(uow.session)
    for user_id in dict.fromkeys(user_ids):
        if await pod_member_id(uow, channel.pod_id, user_id) is None:
            raise unprocessable("Only members of the pod can be added")
        await members.add_person(
            channel, Principal.member(user_id), added_by_user_id=added_by
        )


async def seat_agents(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    agents: list[tuple[UUID, AgentAnswers]],
    *,
    added_by: UUID,
) -> None:
    """Put the pod's agents in a channel, each answering as asked."""
    if not agents:
        return
    known = await Names(uow).agents(
        pod_id=channel.pod_id, agent_ids=[agent_id for agent_id, _ in agents]
    )
    members = MemberRepository(uow.session)
    for agent_id, answers in dict(agents).items():
        if agent_id not in known:
            raise unprocessable("Only the pod's agents can be added")
        await members.add_agent(
            channel,
            agent_id,
            answers=answers,
            added_by_user_id=added_by,
        )


# -- the caller's own channel ------------------------------------------------------


async def _direct_of(uow: SqlAlchemyUnitOfWork, caller: Caller) -> Channel | None:
    repository = ChannelRepository(uow.session)
    for archived in (False, True):
        found = await repository.list_for(
            caller.viewer, kind=ChannelKind.DIRECT, archived=archived, limit=1
        )
        if found:
            return found[0]
    return None


@router.get(
    "/me",
    operation_id="channel.me.get",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def get_my_channel(
    pod_id: UUID, uow: UoWDep, caller: CallerDep
) -> ChannelDetailResponse:
    """The reader's own channel with the pod; 404 until it is made."""
    channel = await _direct_of(uow, caller)
    if channel is None:
        raise missing()
    return await channel_detail(uow, caller, channel)


@router.post(
    "/me",
    operation_id="channel.me.create",
    response_model=ChannelDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def create_my_channel(
    pod_id: UUID, uow: UoWDep, caller: CallerDep, response: Response
) -> ChannelDetailResponse:
    """Make the reader's own channel with the pod; 200 with it if it exists."""
    existing = await _direct_of(uow, caller)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return await channel_detail(uow, caller, existing)
    channel = await ChannelRepository(uow.session).direct(
        pod_id=caller.pod_id, user_id=caller.user_id
    )
    await uow.commit()
    return await channel_detail(uow, caller, channel)


# -- one channel ---------------------------------------------------------------------


@router.get(
    "/{channel_id}",
    operation_id="channel.get",
    response_model=ChannelDetailResponse,
    dependencies=[require_action(Permissions.CONVERSATION_READ)],
)
async def get_channel(
    pod_id: UUID, channel_id: UUID, uow: UoWDep, caller: CallerDep
) -> ChannelDetailResponse:
    """One channel, with the places it is fed from, its members and agents,
    and how the reader hears of it."""
    channel = await visible(uow, caller, channel_id)
    return await channel_detail(uow, caller, channel)


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
    uow: UoWDep,
    caller: CallerDep,
) -> ChannelDetailResponse:
    """Change exactly the fields named; `null` clears.

    A group: `title`, `topic`, `visibility`, `archived` -- its owners' or an
    admin's. A contact's channel: `agents_answer`, `hand_back_after_hours`,
    `status`, `assignee` -- for who may write to contacts. Switching the agent
    on hands the contact back to it now: nobody is assigned.
    """
    channel = await visible(uow, caller, channel_id)
    named = set(body.model_fields_set)
    _check_fields(channel, body, named)
    if channel.kind is ChannelKind.GROUP:
        await require_manage(uow, caller, channel)
        await _change_group(uow, channel, body, named)
    elif named:
        require_contact_message(caller)
        await _change_contact(uow, channel, body, named)
    await uow.commit()
    changed = await ChannelRepository(uow.session).get(channel.id)
    return await channel_detail(uow, caller, changed or channel)


def _check_fields(
    channel: Channel, body: ChannelUpdateRequest, named: set[str]
) -> None:
    allowed = {
        ChannelKind.GROUP: GROUP_FIELDS,
        ChannelKind.CONTACT: CONTACT_FIELDS,
        ChannelKind.DIRECT: frozenset[str](),
    }[channel.kind]
    wrong = named - allowed
    if wrong:
        raise conflict(
            f"A {channel.kind.value} channel has no {', '.join(sorted(wrong))} to change"
        )
    cleared = {field for field in named & NOT_CLEARABLE if getattr(body, field) is None}
    if cleared:
        raise unprocessable(f"Cannot clear {', '.join(sorted(cleared))}")


async def _change_group(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    body: ChannelUpdateRequest,
    named: set[str],
) -> None:
    values: dict[str, object] = {}
    if "title" in named:
        values["title"] = body.title
    if "topic" in named:
        values["topic"] = body.topic or None
    if "visibility" in named:
        assert body.visibility is not None
        if body.visibility is Visibility.PUBLIC and any(
            source.is_private
            for source in await SourceRepository(uow.session).of_channel(channel.id)
        ):
            raise conflict("A private chat's channel stays private")
        values["visibility"] = body.visibility.value
    if "archived" in named:
        values["archived_at"] = now() if body.archived else None
    await ChannelRepository(uow.session).change(channel.id, values)


async def _change_contact(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    body: ChannelUpdateRequest,
    named: set[str],
) -> None:
    repository = ChannelRepository(uow.session)
    values: dict[str, object] = {}
    for field in ("agents_answer", "hand_back_after_hours"):
        if field in named:
            values[field] = getattr(body, field)
    if "status" in named:
        assert body.status is not None
        values["status"] = body.status.value
    handing_back = "agents_answer" in named and body.agents_answer
    if handing_back and body.assignee is not None:
        raise unprocessable("Switching the agent on hands the contact back to it")
    if "assignee" in named and body.assignee is not None:
        if not await pod_member_holds(
            uow, channel.pod_id, body.assignee, Permissions.CONTACT_READ
        ):
            raise unprocessable("Only a member who may see contacts can be assigned")
        await repository.assign(channel.id, body.assignee)
    elif handing_back or "assignee" in named:
        await repository.assign(channel.id, None)
    await repository.change(channel.id, values)


@router.delete(
    "/{channel_id}",
    operation_id="channel.delete",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_action(Permissions.CONVERSATION_WRITE)],
)
async def delete_channel(
    pod_id: UUID, channel_id: UUID, uow: UoWDep, caller: CallerDep
) -> None:
    """Delete a group made in Lemma, and its timeline: its owners' or an
    admin's. A channel an outside place feeds is archived instead."""
    channel = await visible(uow, caller, channel_id)
    if channel.kind is not ChannelKind.GROUP:
        raise conflict("Only a group can be deleted")
    if await SourceRepository(uow.session).of_channel(channel.id):
        raise conflict("An outside place feeds this channel: archive it instead")
    await require_manage(uow, caller, channel)
    await ChannelRepository(uow.session).delete(channel.id)
    await uow.commit()
