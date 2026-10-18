"""What the channels module announces.

``ChannelMessagePosted`` is emitted in the transaction that adds a line to a
timeline, for whatever must follow it -- a member's post going out to the
place it was written for, a contact's agent hearing a member stepped in.
A submodule rather than ``contracts/__init__``: it pulls in the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import Fact, pod_namespace
from app.modules.channels.domain.entities import (
    ChannelKind,
    InteractionState,
    SenderKind,
)


class ChannelMessagePosted(Fact):
    pod_id: UUID
    channel_id: UUID
    channel_kind: ChannelKind | None = None
    message_id: UUID
    sender_kind: SenderKind
    #: Where it was said, or ``None`` for a line written in Lemma.
    source_id: UUID | None = None
    members_only: bool = False
    #: The member who wrote it, for a member's line.
    sender_user_id: UUID | None = None
    #: The agents it names, which answer a member's line written in Lemma.
    mentioned_agent_ids: tuple[UUID, ...] = ()
    #: The thread it is a reply in.
    thread_root_id: UUID | None = None
    #: Written in Lemma for its source: whatever owns that platform carries it
    #: there. Never set for a line heard on a platform, nor one already sent.
    deliver: bool = False

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


class ChannelQuestionClosed(Fact):
    """A question a line put to somebody stopped waiting: answered, cancelled
    or expired. Announced whichever way it closed, for the badges and the
    asking side's bookkeeping."""

    pod_id: UUID
    channel_id: UUID
    message_id: UUID
    ref: str | None = None
    state: InteractionState
    awaited_user_id: UUID | None = None

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


__all__ = ["ChannelMessagePosted", "ChannelQuestionClosed"]
