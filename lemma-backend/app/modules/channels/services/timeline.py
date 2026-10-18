"""Writing to a channel's timeline, and reading it as somebody may.

Posting makes the person who spoke a participant -- a member or a contact,
never an agent, which is attached instead -- and so does being named or asked
something there: the line has to reach them. Each line is announced
(``ChannelMessagePosted``) in the same transaction, for whatever has to follow
it: delivering a member's post, waking an agent that was named.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.modules.channels.contracts.facts import ChannelMessagePosted
from app.modules.channels.domain.entities import (
    Channel,
    ChannelMessage,
    Principal,
    Sender,
    SenderKind,
)
from app.modules.channels.domain.projection import Reader, readable
from app.modules.channels.infrastructure.inbox import InboxReads
from app.modules.channels.infrastructure.lines import (
    LineRepository,
    NewLine,
    NewQuestion,
)
from app.modules.channels.infrastructure.repository import ChannelRepository


#: The longest line a timeline keeps. A message is what people saw, so this is
#: generous -- a pasted document is cut, not refused.
MAX_TEXT_CHARS = 20_000
#: The most people and agents one line may name.
MAX_MENTIONS = 50


class ChannelNotFound(LookupError):
    """The channel is gone, or was never this pod's."""


class Timeline:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.channels = ChannelRepository(uow.session)
        self.lines = LineRepository(uow.session)
        self.inbox = InboxReads(uow.session)

    async def post(
        self,
        channel: Channel,
        *,
        sender: Sender,
        text: str,
        source_id: UUID | None = None,
        line: NewLine | None = None,
        question: NewQuestion | None = None,
        mentions: Sequence[Principal] = (),
        mentioned_agents: Sequence[UUID] = (),
    ) -> ChannelMessage | None:
        """The line, posted; ``None`` when there was nothing to say.

        A platform message heard before is the line it already made, and is
        not announced again.
        """
        line = line or NewLine()
        body = text.strip()[:MAX_TEXT_CHARS]
        if not body and not line.parts and question is None:
            return None
        # Seated first: somebody named here joins before the line naming them,
        # so it counts as new to them.
        await self._seat(channel, sender, question=question, mentions=mentions)
        message, written = await self.lines.append(
            channel=channel,
            sender=sender,
            text=body,
            source_id=source_id,
            line=line,
            question=question,
        )
        if not written:
            return message
        people = _unique(mentions)[:MAX_MENTIONS]
        agents = list(dict.fromkeys(mentioned_agents))[:MAX_MENTIONS]
        await self.inbox.mention(
            message_id=message.id,
            channel_id=channel.id,
            pod_id=channel.pod_id,
            people=people,
            agent_ids=agents,
        )
        await facts.emit(
            self.uow,
            ChannelMessagePosted(
                pod_id=channel.pod_id,
                channel_id=channel.id,
                channel_kind=channel.kind,
                message_id=message.id,
                sender_kind=sender.kind,
                source_id=source_id,
                members_only=line.members_only,
                sender_user_id=sender.user_id,
                mentioned_agent_ids=tuple(agents),
                thread_root_id=line.thread_root_id,
            ),
        )
        return message

    async def _seat(
        self,
        channel: Channel,
        sender: Sender,
        *,
        question: NewQuestion | None,
        mentions: Sequence[Principal],
    ) -> None:
        """Whoever spoke, was named or was asked is in the channel now."""
        speaker = _principal_of(sender)
        if speaker is not None:
            await self.channels.join(channel=channel, principal=speaker)
        elif sender.agent_id is not None:
            await self.channels.attach_agent(
                channel_id=channel.id, agent_id=sender.agent_id
            )
        named = _unique([*mentions, *([question.awaits] if question else [])])
        for person in named[:MAX_MENTIONS]:
            await self.channels.join(channel=channel, principal=person)

    async def background(
        self, channel_id: UUID, *, reader: Reader, limit: int
    ) -> list[ChannelMessage]:
        """The recent lines ``reader`` may see, oldest first.

        A contact reads only their source, so only their source is read; a
        member reads the whole channel.
        """
        if not reader.is_member and reader.source_id is None:
            return []
        recent = await self.lines.recent(
            channel_id,
            limit=limit,
            source_id=None if reader.is_member else reader.source_id,
        )
        return readable(reader, recent)

    async def channel(self, channel_id: UUID, *, pod_id: UUID) -> Channel:
        found = await self.channels.get(channel_id)
        if found is None or found.pod_id != pod_id:
            raise ChannelNotFound(str(channel_id))
        return found


def _principal_of(sender: Sender) -> Principal | None:
    if sender.kind is SenderKind.MEMBER and sender.user_id is not None:
        return Principal.member(sender.user_id)
    if sender.kind is SenderKind.CONTACT and sender.contact_id is not None:
        return Principal.contact(sender.contact_id)
    return None


def _unique(people: Sequence[Principal]) -> list[Principal]:
    return list(dict.fromkeys(people))
