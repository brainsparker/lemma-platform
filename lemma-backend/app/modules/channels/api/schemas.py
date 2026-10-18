"""The wire shapes of a pod's channels, and the mappers to them."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Participant,
    Principal,
    SenderKind,
    Source,
    SourceKind,
)
from app.modules.channels.domain.projection import withheld_from


class PersonRef(BaseModel):
    """Somebody in a channel, said one way everywhere: a member of the pod, one
    of its contacts, or one of its agents. There is nobody else -- everybody
    outside the pod is a contact."""

    kind: SenderKind
    id: UUID | None = Field(
        default=None,
        description=(
            "The member's user id, the contact's id or the agent's id; absent "
            "for a member or an agent since removed."
        ),
    )
    name: str | None = Field(
        default=None, description="The name they were shown under, where known."
    )


class ChannelSourceResponse(BaseModel):
    id: UUID
    platform: str = Field(
        description="Where it is: TELEGRAM, WHATSAPP, SLACK, RESEND, WEB…"
    )
    kind: SourceKind
    title: str | None = None
    surface_id: UUID | None = None
    last_inbound_at: datetime | None = None


class ChannelParticipantResponse(BaseModel):
    person: PersonRef = Field(description="A member or a contact.")
    last_read_at: datetime | None = None


class ChannelResponse(BaseModel):
    id: UUID
    kind: ChannelKind
    title: str | None = None
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
    created_at: datetime
    last_message_at: datetime | None = None


class ChannelDetailResponse(ChannelResponse):
    sources: list[ChannelSourceResponse] = Field(default_factory=list)
    participants: list[ChannelParticipantResponse] = Field(default_factory=list)


class ChannelListResponse(BaseModel):
    items: list[ChannelResponse]
    next_before: str | None = Field(
        default=None, description="Pass as `before` for the next page."
    )


class ChannelMessageResponse(BaseModel):
    id: UUID
    sender: PersonRef
    on_behalf_of: PersonRef | None = Field(
        default=None,
        description=(
            "For an agent's line: the member or contact it acted for, whose "
            "access the answer was made with."
        ),
    )
    text: str | None = Field(
        default=None, description="Absent where the line is withheld from the reader."
    )
    withheld: bool = Field(
        default=False,
        description=(
            "An answer an agent made with another member's own access: the "
            "reader sees that it was given, and for whom, not what it said."
        ),
    )
    source_id: UUID | None = Field(
        default=None,
        description="Where it was said; absent for a line written in Lemma.",
    )
    members_only: bool = False
    created_at: datetime


class ChannelMessagesResponse(BaseModel):
    items: list[ChannelMessageResponse] = Field(
        description="Oldest first, the newest page unless `before` was given."
    )
    next_before: str | None = Field(
        default=None, description="Pass as `before` for the page before this one."
    )


def channel_response(channel: Channel) -> ChannelResponse:
    return ChannelResponse(
        id=channel.id,
        kind=channel.kind,
        title=channel.title,
        person=(
            _ref(Principal.member(channel.member_user_id))
            if channel.member_user_id
            else _ref(Principal.contact(channel.contact_id))
            if channel.contact_id
            else None
        ),
        handler=(
            _ref(Principal.member(channel.handler_user_id))
            if channel.handler_user_id
            else None
        ),
        created_at=channel.created_at,
        last_message_at=channel.last_message_at,
    )


def detail_response(
    channel: Channel, sources: list[Source], participants: list[Participant]
) -> ChannelDetailResponse:
    return ChannelDetailResponse(
        **channel_response(channel).model_dump(),
        sources=[
            ChannelSourceResponse(
                id=source.id,
                platform=source.platform,
                kind=source.kind,
                title=source.title,
                surface_id=source.surface_id,
                last_inbound_at=source.last_inbound_at,
            )
            for source in sources
        ],
        participants=[
            ChannelParticipantResponse(
                person=_ref(participant.principal),
                last_read_at=participant.last_read_at,
            )
            for participant in participants
        ],
    )


def _ref(principal: Principal, *, name: str | None = None) -> PersonRef:
    if principal.user_id is not None:
        return PersonRef(kind=SenderKind.MEMBER, id=principal.user_id, name=name)
    return PersonRef(kind=SenderKind.CONTACT, id=principal.contact_id, name=name)


def message_response(
    message: ChannelMessage, *, reader_id: UUID
) -> ChannelMessageResponse:
    """One line as the member ``reader_id`` reads it."""
    sender = message.sender
    behalf = sender.on_behalf_of
    withheld = withheld_from(message, user_id=reader_id)
    return ChannelMessageResponse(
        id=message.id,
        sender=PersonRef(
            kind=sender.kind,
            id=sender.user_id or sender.contact_id or sender.agent_id,
            name=sender.display_name,
        ),
        on_behalf_of=_ref(behalf) if behalf else None,
        text=None if withheld else message.text,
        withheld=withheld,
        source_id=message.source_id,
        members_only=message.members_only,
        created_at=message.created_at,
    )
