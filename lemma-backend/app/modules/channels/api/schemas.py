"""The wire shapes of a pod's channels, and the mappers to them.

Everybody is named one way, a ``PersonRef``: a member of the pod, one of its
contacts, or one of its agents -- everybody outside the pod is a contact.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    NotifyLevel,
    Participant,
    Principal,
    SenderKind,
    Source,
    SourceKind,
)
from app.modules.channels.contracts.people import PersonRef
from app.modules.channels.infrastructure.inbox import UNREAD_CAP, Unread

#: A name lookup: what to call each person a page names.
Names = Mapping[Principal, str | None]


class ChannelSourceResponse(BaseModel):
    id: UUID
    platform: str = Field(
        description="Where it is: TELEGRAM, WHATSAPP, SLACK, TEAMS, RESEND, WEB…"
    )
    kind: SourceKind
    title: str | None = None
    surface_id: UUID | None = None
    last_inbound_at: datetime | None = None
    pending: bool = Field(
        default=False,
        description="A group the bot asked the platform for, not confirmed yet.",
    )


class SourceSummary(BaseModel):
    platform: str
    title: str | None = None


class UnreadResponse(BaseModel):
    lines: int = Field(
        default=0,
        description=f"New lines since the reader last read it; stops at {UNREAD_CAP}.",
    )
    mentions: int = Field(default=0, description="Lines naming the reader, unread.")
    waiting: int = Field(
        default=0, description="Questions here waiting on the reader's answer."
    )


class AttentionResponse(BaseModel):
    since: datetime
    reason: str | None = None


class LinePreview(BaseModel):
    sender: PersonRef
    text: str = Field(description="The start of what was said.")
    created_at: datetime


class ChannelResponse(BaseModel):
    id: UUID
    kind: ChannelKind
    title: str | None = None
    topic: str | None = None
    person: PersonRef | None = Field(
        default=None,
        description=(
            "Whose channel it is: the member's for a DIRECT one, the contact's "
            "for a CONTACT one; absent for a GROUP."
        ),
    )
    handler: PersonRef | None = Field(
        default=None,
        description="A member answering by hand; agents do not answer meanwhile.",
    )
    attention: AttentionResponse | None = Field(
        default=None, description="The channel asks for a person, and why."
    )
    archived: bool = False
    created_at: datetime
    last_message_at: datetime | None = None
    sources: list[SourceSummary] = Field(
        default_factory=list, description="The outside places feeding it."
    )
    unread: UnreadResponse = Field(default_factory=UnreadResponse)
    last_line: LinePreview | None = None


class ChannelParticipantResponse(BaseModel):
    person: PersonRef = Field(description="A member or a contact.")
    last_read_at: datetime | None = None
    notify: NotifyLevel = NotifyLevel.ALL


class MyChannelSettings(BaseModel):
    notify: NotifyLevel = NotifyLevel.ALL
    notify_via: list[str] | None = Field(
        default=None,
        description="Where lines reach them outside the app; absent for the pod's rule.",
    )
    last_read_at: datetime | None = None
    joined: bool = False


class ChannelDetailResponse(ChannelResponse):
    source_places: list[ChannelSourceResponse] = Field(default_factory=list)
    participants: list[ChannelParticipantResponse] = Field(default_factory=list)
    me: MyChannelSettings = Field(default_factory=MyChannelSettings)


class ChannelListResponse(BaseModel):
    items: list[ChannelResponse]
    next_before: str | None = Field(
        default=None, description="Pass as `before` for the next page."
    )


class ChannelCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    topic: str | None = Field(default=None, max_length=500)
    members: list[UUID] = Field(
        default_factory=list, max_length=200, description="Members to add, by user id."
    )


class ChannelUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    topic: str | None = Field(default=None, max_length=500)
    archived: bool | None = None


class ParticipantsAddRequest(BaseModel):
    members: list[UUID] = Field(min_length=1, max_length=200)


class NotifyRequest(BaseModel):
    notify: NotifyLevel
    notify_via: list[str] | None = Field(default=None, max_length=10)


def ref(principal: Principal, names: Names) -> PersonRef:
    if principal.user_id is not None:
        return PersonRef(
            kind=SenderKind.MEMBER, id=principal.user_id, name=names.get(principal)
        )
    return PersonRef(
        kind=SenderKind.CONTACT, id=principal.contact_id, name=names.get(principal)
    )


def channel_people(channel: Channel) -> list[Principal]:
    """Everybody a channel's own row names."""
    people: list[Principal] = []
    if channel.member_user_id:
        people.append(Principal.member(channel.member_user_id))
    if channel.contact_id:
        people.append(Principal.contact(channel.contact_id))
    if channel.handler_user_id:
        people.append(Principal.member(channel.handler_user_id))
    return people


def _person(channel: Channel, names: Names) -> PersonRef | None:
    if channel.member_user_id:
        return ref(Principal.member(channel.member_user_id), names)
    if channel.contact_id:
        return ref(Principal.contact(channel.contact_id), names)
    return None


def channel_response(
    channel: Channel,
    *,
    names: Names | None = None,
    unread: Unread | None = None,
    sources: list[Source] | None = None,
    last_line: LinePreview | None = None,
) -> ChannelResponse:
    names = names or {}
    return ChannelResponse(
        id=channel.id,
        kind=channel.kind,
        title=channel.title,
        topic=channel.topic,
        person=_person(channel, names),
        handler=(
            ref(Principal.member(channel.handler_user_id), names)
            if channel.handler_user_id
            else None
        ),
        attention=(
            AttentionResponse(
                since=channel.attention_at, reason=channel.attention_reason
            )
            if channel.attention_at
            else None
        ),
        archived=channel.archived_at is not None,
        created_at=channel.created_at,
        last_message_at=channel.last_message_at,
        sources=[
            SourceSummary(platform=source.platform, title=source.title)
            for source in sources or []
        ],
        unread=UnreadResponse(
            lines=unread.lines, mentions=unread.mentions, waiting=unread.waiting
        )
        if unread
        else UnreadResponse(),
        last_line=last_line,
    )


def source_response(source: Source) -> ChannelSourceResponse:
    return ChannelSourceResponse(
        id=source.id,
        platform=source.platform,
        kind=source.kind,
        title=source.title,
        surface_id=source.surface_id,
        last_inbound_at=source.last_inbound_at,
        pending=source.is_pending,
    )


def _me(participants: list[Participant], reader_id: UUID) -> MyChannelSettings:
    for participant in participants:
        if participant.principal.user_id == reader_id:
            return MyChannelSettings(
                notify=participant.notify,
                notify_via=(
                    list(participant.notify_via)
                    if participant.notify_via is not None
                    else None
                ),
                last_read_at=participant.last_read_at,
                joined=True,
            )
    return MyChannelSettings()


def detail_response(
    channel: Channel,
    *,
    sources: list[Source],
    participants: list[Participant],
    names: Names,
    unread: Unread | None,
    reader_id: UUID,
) -> ChannelDetailResponse:
    return ChannelDetailResponse(
        **channel_response(
            channel, names=names, unread=unread, sources=sources
        ).model_dump(),
        source_places=[source_response(source) for source in sources],
        participants=[
            ChannelParticipantResponse(
                person=ref(participant.principal, names),
                last_read_at=participant.last_read_at,
                notify=participant.notify,
            )
            for participant in participants
        ],
        me=_me(participants, reader_id),
    )
