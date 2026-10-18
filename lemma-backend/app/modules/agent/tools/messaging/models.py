"""Request/response models for the messaging toolset: channels, and the
people of the pod."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.agent.tools.context import BaseToolResponse

#: The most a contact's run may say when it calls for a person.
MAX_OUTSIDER_MESSAGE_CHARS = 1000
#: The most lines one read returns.
MAX_READ_LINES = 50


# Declared here rather than imported from ``agent_surfaces``, which the agent
# module must not depend on. That leaves two lists that have to agree with no
# compiler to make them, so a test does it instead:
# ``test_the_tool_offers_exactly_the_channels_routing_can_produce``.
class MessageChannel(StrEnum):
    """Where a message goes, when the agent has a reason to say.

    Platforms, not surface ids: you think "reach her on WhatsApp", not "send
    through surface 3f2a…". Every mail provider is one `email`.
    """

    EMAIL = "email"
    SLACK = "slack"
    TEAMS = "teams"
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"


class ChannelPostRequest(BaseModel):
    text: str = Field(
        description=(
            "Markdown -- every surface renders it. Write it to them, not about them."
        )
    )
    to: str | None = Field(
        default=None,
        description=(
            "A pod member id, user id, or email address: it goes to their own "
            "channel, and on to the chat app they last used or their email. "
            "Use list_pod_members to find someone by name."
        ),
    )
    channel_id: UUID | None = Field(
        default=None,
        description="A channel to write in instead, from channel_list.",
    )
    wants_answer: bool = Field(
        default=False,
        description=(
            "Whether you need an answer. It then waits on them in their inbox, "
            "and you get a fresh turn here once everyone you asked has answered."
        ),
    )
    platform: MessageChannel | None = Field(
        default=None,
        description=(
            "Only with `to`, and only for a reason: by default it goes where "
            "they last spoke to you. Named, it is honoured or refused, never "
            "swapped -- read `reachable_on` from list_pod_members first."
        ),
    )


class ChannelPostResponse(BaseToolResponse):
    message_id: UUID | None = None
    channel_id: UUID | None = None
    delivered_via: str | None = None


class ChannelReadRequest(BaseModel):
    channel_id: UUID | None = Field(
        default=None, description="The channel to read, from channel_list."
    )
    asked_by_me: bool = Field(
        default=False,
        description=(
            "Instead: the questions this conversation put to people, and their "
            "answers. Read it when a turn opens saying they have answered."
        ),
    )
    limit: int = Field(default=20, ge=1, le=MAX_READ_LINES)


class LineSummary(BaseModel):
    id: UUID
    sender: str
    text: str | None
    at: str
    question_state: str | None = None
    answer: dict[str, object] | None = None
    awaits: str | None = None


class ChannelReadResponse(BaseToolResponse):
    lines: list[LineSummary] = Field(default_factory=list)


class ChannelSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=MAX_READ_LINES)


class ChannelSearchResponse(BaseToolResponse):
    lines: list[LineSummary] = Field(default_factory=list)


class ChannelSummary(BaseModel):
    id: UUID
    kind: str
    title: str | None
    places: list[str] = Field(default_factory=list)


class ChannelListResponse(BaseToolResponse):
    channels: list[ChannelSummary] = Field(default_factory=list)


class ChannelListRequest(BaseModel):
    kind: str | None = Field(
        default=None, description="DIRECT, CONTACT or GROUP; omit for all."
    )


class AnswerQuestionRequest(BaseModel):
    question_id: UUID = Field(description="The question's id, from the open questions.")
    answer: dict[str, object] = Field(
        description=(
            "What they said: `{text}` for a question, `{decision}` (APPROVE_ONCE "
            "or DENY) for an approval, the form's fields for a form."
        )
    )


class AnswerQuestionResponse(BaseToolResponse):
    pass


class AskAPersonRequest(BaseModel):
    reason: str = Field(
        max_length=MAX_OUTSIDER_MESSAGE_CHARS,
        description="What they need that you cannot give them, in a sentence.",
    )


class AskAPersonResponse(BaseToolResponse):
    pass


class ListPodMembersRequest(BaseModel):
    search: str = Field(
        default="",
        description="Name or email fragment, e.g. 'priya'. Omit to list everyone.",
    )
    limit: int = Field(default=50, ge=1, le=200)


class PodMemberSummary(BaseModel):
    # Named `to` because that is the whole job: the model copies one value into
    # channel_post without having to know which of three id kinds it holds.
    to: str = Field(description="Pass this verbatim as channel_post's `to`.")
    name: str | None = None
    email: str | None = None
    role: str | None = None
    is_you: bool = Field(
        default=False, description="True for the person this run belongs to."
    )
    reachable_on: list[str] = Field(
        default_factory=list,
        description=(
            "Channels that can carry a message to them right now — pass one as "
            "channel_post's `platform`. Empty means only their channel in Lemma."
        ),
    )


class ListPodMembersResponse(BaseToolResponse):
    members: list[PodMemberSummary] = Field(default_factory=list)
    total_matched: int = Field(
        default=0, description="Matches found, which may exceed those returned."
    )
    truncated: bool = Field(
        default=False, description="True when narrowing `search` would help."
    )
