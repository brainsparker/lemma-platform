"""A channel: one shared place where a pod's members, contacts and agents talk.

A channel holds a timeline -- every line people saw, who said it, when and
where -- numbered per channel (``seq``), and is fed by any number of
**sources**: places in an external account (a WhatsApp chat, a Telegram
group, a Slack channel, an email thread, a web chat). A source joins a channel
only because the channel was made for it or a member linked it; nothing infers
that two places are one.

``DIRECT``  one member and the pod: what the pod tells them, their private
            chats with its bot on every platform.
``CONTACT`` one contact and the pod: their private chats, web chat, emails --
            the support inbox for that person.
``GROUP``   several people: made in Lemma, or a platform's group or channel.
            ``public`` (every member reads it) or ``private`` (its members).

An agent's working -- tool calls, reasoning -- is never a line. That stays in
the conversation: one agent working for one person, placed in the channel.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.modules.channels.domain.parts import Part


class ChannelKind(StrEnum):
    DIRECT = "DIRECT"
    CONTACT = "CONTACT"
    GROUP = "GROUP"


class Visibility(StrEnum):
    #: Every member of the pod may read it.
    PUBLIC = "public"
    #: Its members only.
    PRIVATE = "private"


class ContactStatus(StrEnum):
    """Where a contact's conversation stands, as the pod's inbox sees it."""

    OPEN = "open"
    #: Answered; the next move is the contact's.
    WAITING_ON_CONTACT = "waiting_on_contact"
    CLOSED = "closed"


class SourceKind(StrEnum):
    #: One person's chat with the bot.
    PRIVATE = "PRIVATE"
    #: A place several people read.
    SHARED = "SHARED"


class SenderKind(StrEnum):
    MEMBER = "MEMBER"
    CONTACT = "CONTACT"
    AGENT = "AGENT"


class NotifyLevel(StrEnum):
    """Which lines of a channel reach a member outside the app."""

    ALL = "ALL"
    MENTIONS = "MENTIONS"
    NONE = "NONE"


class MemberRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"
    #: A contact in a channel.
    GUEST = "guest"


class AgentAnswers(StrEnum):
    """When an agent in a channel answers."""

    #: When a line names it.
    MENTIONED = "MENTIONED"
    #: Every line said there.
    EVERY_LINE = "EVERY_LINE"


class QuestionKind(StrEnum):
    #: A question, with choices or free text (``ask_user``, another member's
    #: agent asking).
    ASK = "ASK"
    #: Whether an agent may go ahead (``request_approval``). Only a person's
    #: tap answers it.
    APPROVAL = "APPROVAL"
    #: A workflow's form.
    FORM = "FORM"


class QuestionState(StrEnum):
    OPEN = "OPEN"
    #: An answer is being taken; nobody else may answer meanwhile.
    ANSWERING = "ANSWERING"
    ANSWERED = "ANSWERED"
    #: What asked ended, or was stopped, before an answer came.
    CANCELLED = "CANCELLED"
    #: Its deadline passed.
    EXPIRED = "EXPIRED"

    @property
    def is_closed(self) -> bool:
        return self in (
            QuestionState.ANSWERED,
            QuestionState.CANCELLED,
            QuestionState.EXPIRED,
        )


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    #: Did not go; will be tried again.
    FAILED = "FAILED"
    #: Not sent, on purpose: a rule said not to (opt-out, a window, a cap).
    SKIPPED = "SKIPPED"
    #: Tried as often as it will be.
    DEAD = "DEAD"


class Principal(BaseModel):
    """A person: a member of the pod, or one of its contacts. Exactly one."""

    model_config = ConfigDict(frozen=True)

    user_id: UUID | None = None
    contact_id: UUID | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.user_id is None) == (self.contact_id is None):
            raise ValueError("A principal is a member or a contact, exactly one")
        return self

    @classmethod
    def member(cls, user_id: UUID) -> Principal:
        return cls(user_id=user_id)

    @classmethod
    def contact(cls, contact_id: UUID) -> Principal:
        return cls(contact_id=contact_id)

    @property
    def is_contact(self) -> bool:
        return self.contact_id is not None


class ExternalAccount(BaseModel):
    """An account a pod's bot speaks through: a Slack workspace, a Teams
    tenant, a WhatsApp number, an inbox, a web widget."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    pod_id: UUID
    platform: str
    account_key: str
    surface_id: UUID | None = None


class Channel(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    pod_id: UUID
    kind: ChannelKind
    visibility: Visibility
    title: str | None = None
    topic: str | None = None
    #: Whose DIRECT channel this is.
    member_user_id: UUID | None = None
    #: Whose CONTACT channel this is.
    contact_id: UUID | None = None
    created_by_user_id: UUID | None = None
    created_at: datetime
    archived_at: datetime | None = None
    # -- a contact's channel ---------------------------------------------------
    agents_answer: bool = True
    hand_back_after_hours: int = 12
    assignee_user_id: UUID | None = None
    assigned_at: datetime | None = None
    assignee_last_line_at: datetime | None = None
    status: ContactStatus = ContactStatus.OPEN
    attention_at: datetime | None = None
    attention_reason: str | None = None
    #: When the contact was last told a person will reply (``acknowledge``).
    acknowledged_at: datetime | None = None
    # -- the timeline's head ---------------------------------------------------
    last_seq: int = 0
    last_line_id: UUID | None = None
    last_line_at: datetime

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class Source(BaseModel):
    """One outside place a channel is fed by, in one external account."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    channel_id: UUID
    pod_id: UUID
    account_id: UUID
    #: The installation the account is reached through now (the account's).
    surface_id: UUID | None
    platform: str
    kind: SourceKind
    #: The platform keeps it to its members.
    is_private: bool = True
    external_channel_id: str | None = None
    external_user_id: str | None = None
    title: str | None = None
    last_inbound_at: datetime | None = None
    #: How the account writes into the place cold: the adapter's own shape.
    delivery_ref: dict[str, object] | None = None
    # -- a shared place: its bot-side settings ---------------------------------
    owner_user_id: UUID | None = None
    answers_outsiders: bool = True
    shared_externally: bool = False
    invite_link: str | None = None
    request_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_pending(self) -> bool:
        """A group the bot asked a platform to create, not confirmed yet."""
        return self.external_channel_id is None and self.external_user_id is None

    @property
    def welcomes_outsiders(self) -> bool:
        """Whether a stranger's question here gets an answer: the pod wants it,
        and somebody answers for it."""
        return self.answers_outsiders and self.owner_user_id is not None


class Member(BaseModel):
    """Somebody in a channel: a member of the pod, a contact, or an agent."""

    model_config = ConfigDict(frozen=True)

    channel_id: UUID
    user_id: UUID | None = None
    contact_id: UUID | None = None
    agent_id: UUID | None = None
    role: MemberRole = MemberRole.MEMBER
    notify: NotifyLevel = NotifyLevel.ALL
    #: Platforms they chose to be reached on, or ``None`` for where they last
    #: spoke to the pod.
    notify_via: tuple[str, ...] | None = None
    muted_until: datetime | None = None
    #: An agent's: when it answers here.
    answers: AgentAnswers | None = None
    joined_at: datetime | None = None

    @property
    def principal(self) -> Principal | None:
        if self.user_id is not None:
            return Principal.member(self.user_id)
        if self.contact_id is not None:
            return Principal.contact(self.contact_id)
        return None


class Sender(BaseModel):
    """Who said a line, and -- for an agent -- whom it said it for."""

    model_config = ConfigDict(frozen=True)

    kind: SenderKind
    user_id: UUID | None = None
    contact_id: UUID | None = None
    agent_id: UUID | None = None
    #: For an agent: the person it acted for, whose access it used.
    on_behalf_of: Principal | None = None
    #: What to call them on the timeline, as the platform named them.
    display_name: str | None = None

    @model_validator(mode="after")
    def _one_sender(self) -> Self:
        """Names nobody but its own kind -- and, once that person or agent is
        gone, nobody at all: the line stays, said by somebody since removed."""
        named = {
            SenderKind.MEMBER: self.user_id,
            SenderKind.CONTACT: self.contact_id,
            SenderKind.AGENT: self.agent_id,
        }
        if any(value is not None for kind, value in named.items() if kind != self.kind):
            raise ValueError(f"A {self.kind.value} sender names only one")
        if self.kind is not SenderKind.AGENT and self.on_behalf_of is not None:
            raise ValueError("Only an agent acts on somebody's behalf")
        return self

    @classmethod
    def member(cls, user_id: UUID, *, display_name: str | None = None) -> Sender:
        return cls(kind=SenderKind.MEMBER, user_id=user_id, display_name=display_name)

    @classmethod
    def contact(cls, contact_id: UUID, *, display_name: str | None = None) -> Sender:
        return cls(
            kind=SenderKind.CONTACT, contact_id=contact_id, display_name=display_name
        )

    @classmethod
    def agent(
        cls,
        agent_id: UUID,
        *,
        on_behalf_of: Principal,
        display_name: str | None = None,
    ) -> Sender:
        return cls(
            kind=SenderKind.AGENT,
            agent_id=agent_id,
            on_behalf_of=on_behalf_of,
            display_name=display_name,
        )

    @property
    def principal(self) -> Principal | None:
        """The person who said it: a member or a contact; not an agent."""
        if self.user_id is not None:
            return Principal.member(self.user_id)
        if self.contact_id is not None:
            return Principal.contact(self.contact_id)
        return None

    @property
    def used_a_members_access(self) -> bool:
        """An agent's line written with one member's own access.

        One whose principal is no longer known counts: it fails closed, so no
        contact reads an answer nobody can say was made for them.
        """
        return self.kind is SenderKind.AGENT and (
            self.on_behalf_of is None or not self.on_behalf_of.is_contact
        )


class Question(BaseModel):
    """What a line asks of one person, and what became of it.

    ``question`` is the asker's own shape -- choices, a form's schema, the tool
    an approval is for. ``ref`` names the asker and what an answer resumes.
    """

    model_config = ConfigDict(frozen=True)

    message_id: UUID
    channel_id: UUID
    pod_id: UUID
    ref: str
    batch_id: str | None = None
    kind: QuestionKind
    state: QuestionState
    question: dict[str, object]
    awaits: Principal
    answer: dict[str, object] | None = None
    answered_by: Principal | None = None
    answered_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.state is QuestionState.OPEN

    @property
    def asker(self) -> str:
        """What asked it: the ref's first part (``tool``, ``wf``, ``msg``)."""
        return self.ref.partition(":")[0]


class ChannelMessage(BaseModel):
    """One line of a channel's timeline."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    channel_id: UUID
    pod_id: UUID
    seq: int
    sender: Sender
    text: str
    created_at: datetime
    client_message_id: str | None = None
    #: Where it was said, or ``None`` for a line written in Lemma.
    source_id: UUID | None = None
    #: A note for the pod's members only: never delivered, never shown outside.
    members_only: bool = False
    parts: tuple[Part, ...] = ()
    conversation_id: UUID | None = None
    agent_run_id: UUID | None = None
    external_message_id: str | None = None
    external_thread_id: str | None = None
    #: On a line a person said on a platform: what answers it in place.
    reply_ref: dict[str, object] | None = None
    reply_to_id: UUID | None = None
    #: The line a thread hangs from, on every reply in it.
    thread_root_id: UUID | None = None
    edited_at: datetime | None = None
    deleted_at: datetime | None = None
    #: The question it asks, if it asks one.
    question: Question | None = None
    #: On a thread's root, as read with it: how many replies, and the last's time.
    reply_count: int = 0
    last_reply_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class Delivery(BaseModel):
    """One send of a line outside Lemma, and what became of it."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    message_id: UUID
    pod_id: UUID
    source_id: UUID | None = None
    recipient_user_id: UUID | None = None
    platform: str | None = None
    status: DeliveryStatus
    reason: str | None = None
    external_message_id: str | None = None
    attempts: int = 0
    next_attempt_at: datetime | None = None
    #: While a sender holds it (``claim``); cleared with the outcome.
    claimed_at: datetime | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None
