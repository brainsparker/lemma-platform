"""Who may read which line of a channel. One rule, in one place.

A channel merges several sources into one timeline, and the people reading it
are not all entitled to all of it. Every reader -- the timeline a member opens,
the background a run is given, a search -- asks this module, so the answer
cannot drift between them:

* **A member** reads everything: every source, every private note, every
  answer whoever it was made for. What a contact wrote reaches a member's run
  marked as somebody outside the pod, never as an instruction. Where a member
  reads the timeline themselves, though, an answer an agent made with another
  member's own access is shown as given but not what it said
  (``withheld_from``): they may not be able to see what it was made from.
* **A contact** -- a run answering them -- reads only what was said where they
  are: the lines from their own source, and none of those an agent wrote with
  a member's own access. Never a private note. A line is where they are when
  it was heard there or carried there from Lemma (``place_id``).
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.channels.domain.entities import (
    ChannelMessage,
    Principal,
    SenderKind,
)


class Reader(BaseModel):
    """Who is reading: a member, or a contact where they are speaking from."""

    model_config = ConfigDict(frozen=True)

    #: The contact reading, or ``None`` for a member.
    contact_id: UUID | None = None
    #: The source the contact is reading from. A contact reads nothing else.
    source_id: UUID | None = None

    @classmethod
    def member(cls) -> Reader:
        return cls()

    @classmethod
    def contact(cls, contact_id: UUID, *, source_id: UUID | None) -> Reader:
        return cls(contact_id=contact_id, source_id=source_id)

    @property
    def is_member(self) -> bool:
        return self.contact_id is None


def may_read(reader: Reader, message: ChannelMessage) -> bool:
    """Whether ``reader`` may see ``message``."""
    if reader.is_member:
        return True
    if message.members_only:
        return False
    if reader.source_id is None or message.place_id != reader.source_id:
        return False
    return not message.sender.used_a_members_access


def readable(
    reader: Reader, messages: Iterable[ChannelMessage]
) -> list[ChannelMessage]:
    """The messages ``reader`` may see, in the order given."""
    return [message for message in messages if may_read(reader, message)]


def withheld_from(
    message: ChannelMessage, *, user_id: UUID, in_their_direct: bool = False
) -> bool:
    """Whether the member ``user_id``, reading the timeline, is shown only that
    this answer was given -- not what it said.

    An agent's answer made with one member's own access is that member's to
    read. It was posted where the people in the chat read it, but a member of
    the pod reading the timeline need not be one of them, and may not be able
    to see what it was made from. One whose principal is no longer known is
    withheld from everybody. Never withheld: a line in the reader's own direct
    channel, which the agent posted there to tell them, and a question put to
    the reader, which they have to read to answer.
    """
    sender = message.sender
    if sender.kind is not SenderKind.AGENT or not sender.used_a_members_access:
        return False
    if in_their_direct:
        return False
    question = message.question
    if question is not None and question.awaits == Principal.member(user_id):
        return False
    behalf = sender.on_behalf_of
    return behalf is None or behalf.user_id != user_id
