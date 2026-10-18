"""The wire shapes of a channel's lines, the questions on them, and the inbox."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.channels.api.schemas import (
    ChannelResponse,
    LinePreview,
    Names,
    PersonRef,
    ref,
)
from app.modules.channels.domain.entities import (
    ChannelMessage,
    InteractionKind,
    InteractionState,
    Principal,
    SenderKind,
)
from app.modules.channels.domain.projection import withheld_from

#: How much of a line a preview shows.
PREVIEW_CHARS = 140

JsonObject = dict[str, object]


class InteractionResponse(BaseModel):
    kind: InteractionKind
    state: InteractionState
    question: JsonObject = Field(
        description=(
            "What is asked: a `prompt`, and `choices` or a `form` where the "
            "asking side offers them."
        )
    )
    awaits: PersonRef | None = Field(
        default=None, description="The one person who may answer it."
    )
    answer: JsonObject | None = None
    answered_at: datetime | None = None
    answered_by: PersonRef | None = None


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
        default=None,
        description="Absent where the line is withheld from the reader, or deleted.",
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
    conversation_id: UUID | None = Field(
        default=None, description="For an agent's line: the conversation it came from."
    )
    parts: list[JsonObject] = Field(default_factory=list)
    reply_to_id: UUID | None = None
    thread_root_id: UUID | None = Field(
        default=None, description="The line this one is a reply in the thread of."
    )
    reply_count: int = Field(default=0, description="On a thread's first line.")
    last_reply_at: datetime | None = None
    edited_at: datetime | None = None
    deleted: bool = False
    interaction: InteractionResponse | None = None
    created_at: datetime


class ChannelMessagesResponse(BaseModel):
    items: list[ChannelMessageResponse] = Field(
        description="Oldest first, the newest page unless `before` was given."
    )
    next_before: str | None = Field(
        default=None, description="Pass as `before` for the page before this one."
    )


class Mention(BaseModel):
    kind: SenderKind
    id: UUID


class ChannelPostRequest(BaseModel):
    text: str = Field(default="", max_length=20_000)
    members_only: bool = Field(
        default=False, description="A note to the pod's members: never sent outside."
    )
    source_id: UUID | None = Field(
        default=None,
        description=(
            "Which outside place it goes to; by default the one the channel "
            "last heard from."
        ),
    )
    thread_root_id: UUID | None = Field(
        default=None, description="Reply in the thread of this line."
    )
    reply_to_id: UUID | None = None
    parts: list[JsonObject] = Field(default_factory=list, max_length=20)
    mentions: list[Mention] = Field(default_factory=list, max_length=50)


class ChannelEditRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class ChannelAnswerRequest(BaseModel):
    answer: Annotated[
        JsonObject,
        Field(
            description=(
                "The answer, in the question's terms: `{choice}` or `{text}` for "
                "a question, `{decision, note?}` for an approval, the form's "
                "fields for a form."
            )
        ),
    ]


class WaitingItem(BaseModel):
    channel: ChannelResponse
    message: ChannelMessageResponse


class InboxTotals(BaseModel):
    channels: int = Field(description="Channels with something new.")
    mentions: int = Field(description="Unread lines naming the reader.")
    waiting: int = Field(description="Questions waiting on the reader's answer.")


class InboxResponse(BaseModel):
    totals: InboxTotals
    waiting: list[WaitingItem] = Field(
        default_factory=list, description="What is waiting on the reader, newest first."
    )


class SearchHit(BaseModel):
    channel_id: UUID
    message: ChannelMessageResponse


class SearchResponse(BaseModel):
    items: list[SearchHit]


def message_people(message: ChannelMessage) -> list[Principal]:
    """Everybody a line names, for one name lookup per page."""
    people: list[Principal] = []
    sender = message.sender
    if sender.user_id:
        people.append(Principal.member(sender.user_id))
    if sender.contact_id:
        people.append(Principal.contact(sender.contact_id))
    if sender.on_behalf_of:
        people.append(sender.on_behalf_of)
    interaction = message.interaction
    if interaction is not None:
        people.extend(
            person
            for person in (interaction.awaits, interaction.answered_by)
            if person is not None
        )
    return people


def _sender(message: ChannelMessage, names: Names) -> PersonRef:
    sender = message.sender
    principal = (
        Principal.member(sender.user_id)
        if sender.user_id
        else Principal.contact(sender.contact_id)
        if sender.contact_id
        else None
    )
    looked_up = names.get(principal) if principal else None
    return PersonRef(
        kind=sender.kind,
        id=sender.user_id or sender.contact_id or sender.agent_id,
        name=looked_up or sender.display_name,
    )


def _interaction(message: ChannelMessage, names: Names) -> InteractionResponse | None:
    interaction = message.interaction
    if interaction is None:
        return None
    return InteractionResponse(
        kind=interaction.kind,
        state=interaction.state,
        question={
            key: value for key, value in interaction.question.items() if key != "ref"
        },
        awaits=ref(interaction.awaits, names) if interaction.awaits else None,
        answer=dict(interaction.answer) if interaction.answer is not None else None,
        answered_at=interaction.answered_at,
        answered_by=(
            ref(interaction.answered_by, names) if interaction.answered_by else None
        ),
    )


def message_response(
    message: ChannelMessage,
    *,
    reader_id: UUID,
    names: Names | None = None,
    in_their_direct: bool = False,
) -> ChannelMessageResponse:
    """One line as the member ``reader_id`` reads it; ``in_their_direct`` when
    it is in the reader's own direct channel."""
    names = names or {}
    behalf = message.sender.on_behalf_of
    hidden = withheld_from(message, user_id=reader_id, in_their_direct=in_their_direct)
    return ChannelMessageResponse(
        id=message.id,
        sender=_sender(message, names),
        on_behalf_of=ref(behalf, names) if behalf else None,
        text=None if hidden or message.is_deleted else message.text,
        withheld=hidden,
        source_id=message.source_id,
        members_only=message.members_only,
        conversation_id=message.conversation_id,
        parts=[] if hidden else [dict(part) for part in message.parts],
        reply_to_id=message.reply_to_id,
        thread_root_id=message.thread_root_id,
        reply_count=message.reply_count,
        last_reply_at=message.last_reply_at,
        edited_at=message.edited_at,
        deleted=message.is_deleted,
        interaction=None if hidden else _interaction(message, names),
        created_at=message.created_at,
    )


def preview(
    message: ChannelMessage,
    *,
    reader_id: UUID,
    names: Names,
    in_their_direct: bool = False,
) -> LinePreview:
    hidden = withheld_from(message, user_id=reader_id, in_their_direct=in_their_direct)
    text = "" if hidden or message.is_deleted else message.text[:PREVIEW_CHARS]
    return LinePreview(
        sender=_sender(message, names), text=text, created_at=message.created_at
    )
