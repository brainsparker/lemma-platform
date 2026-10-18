"""A member writing in a channel: posting, replying in a thread, editing,
deleting, and answering what they were asked.

Where a member's line goes is decided as it is posted, and kept on it as its
source: a contact reads only the lines of their own place, so a reply that
named no place would never reach the run answering them. Unless the member
picks one, it is the place the channel last heard from -- where the
conversation is. A note to members, and a line in a channel fed by no outside
place, go nowhere but the timeline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.channels.contracts.questions import (
    AnswerRefused,
    Answering,
    announce_closed,
    answerer,
)
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Principal,
    Sender,
    SenderKind,
)
from app.modules.channels.infrastructure.lines import LineRepository, NewLine
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.services.timeline import MAX_MENTIONS, Timeline
from app.modules.pod.contracts.members import pod_member_id


class LineRefused(Exception):
    """What a member asked for cannot be done; the reason is theirs to read."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class MemberPost:
    text: str
    members_only: bool = False
    source_id: UUID | None = None
    thread_root_id: UUID | None = None
    reply_to_id: UUID | None = None
    parts: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    mentions: Sequence[Principal] = field(default_factory=tuple)
    mentioned_agents: Sequence[UUID] = field(default_factory=tuple)


class MemberLines:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.channels = ChannelRepository(uow.session)
        self.lines = LineRepository(uow.session)

    async def post(
        self,
        channel: Channel,
        *,
        user_id: UUID,
        post: MemberPost,
        display_name: str | None = None,
    ) -> ChannelMessage:
        thread_root = await self._thread_root(channel, post)
        reply_to = await self._in_channel(channel, post.reply_to_id)
        mentions = await self._members_named(channel, post.mentions)
        source_id = await self._where_it_goes(channel, post)
        message = await Timeline(self.uow).post(
            channel,
            sender=Sender.member(user_id, display_name=display_name),
            text=post.text,
            source_id=source_id,
            line=NewLine(
                members_only=post.members_only,
                parts=tuple(post.parts),
                thread_root_id=thread_root,
                reply_to_id=reply_to,
            ),
            mentions=mentions,
            mentioned_agents=post.mentioned_agents,
        )
        if message is None:
            raise LineRefused("Say something first", status_code=422)
        if channel.kind is ChannelKind.CONTACT and not post.members_only:
            # Answering a contact by hand: their agent stays quiet until the
            # member hands them back.
            await self.channels.set_handler(channel.id, user_id)
        return message

    async def _thread_root(self, channel: Channel, post: MemberPost) -> UUID | None:
        if post.thread_root_id is None:
            return None
        root = await self.lines.get(post.thread_root_id)
        if root is None or root.channel_id != channel.id:
            raise LineRefused("That thread is not in this channel", status_code=404)
        # A reply to a reply joins the thread the first one is in.
        return root.thread_root_id or root.id

    async def _in_channel(
        self, channel: Channel, message_id: UUID | None
    ) -> UUID | None:
        if message_id is None:
            return None
        found = await self.lines.get(message_id)
        if found is None or found.channel_id != channel.id:
            raise LineRefused("That line is not in this channel", status_code=404)
        return found.id

    async def _members_named(
        self, channel: Channel, named: Sequence[Principal]
    ) -> list[Principal]:
        """The people a line names: the pod's members, and the channel's own
        contacts. Anybody else is not named -- nobody else would see it."""
        if len(named) > MAX_MENTIONS:
            raise LineRefused(f"Name at most {MAX_MENTIONS} people", status_code=422)
        here = {
            participant.principal
            for participant in await self.channels.participants(channel.id)
        }
        kept: list[Principal] = []
        for person in dict.fromkeys(named):
            if person.user_id is not None:
                if await pod_member_id(self.uow, channel.pod_id, person.user_id):
                    kept.append(person)
            elif person in here:
                kept.append(person)
        return kept

    async def _where_it_goes(self, channel: Channel, post: MemberPost) -> UUID | None:
        if post.members_only:
            if post.source_id is not None:
                raise LineRefused("A note to members goes nowhere outside")
            return None
        sources = await self.channels.sources_of(channel.id)
        if post.source_id is not None:
            if all(source.id != post.source_id for source in sources):
                raise LineRefused(
                    "That place does not feed this channel", status_code=404
                )
            return post.source_id
        if channel.kind is ChannelKind.DIRECT:
            # Writing in one's own channel is talking to the pod in Lemma.
            return None
        heard = sorted(
            (
                (source.last_inbound_at, source.id)
                for source in sources
                if not source.is_pending and source.last_inbound_at is not None
            ),
            reverse=True,
        )
        return heard[0][1] if heard else None

    async def edit(
        self, channel: Channel, message_id: UUID, *, user_id: UUID, text: str
    ) -> ChannelMessage:
        line = await self._own(channel, message_id, user_id=user_id)
        body = text.strip()
        if not body:
            raise LineRefused("Say something, or delete it", status_code=422)
        edited = await self.lines.edit(line.id, text=body)
        if edited is None:
            raise LineRefused("That line was deleted")
        return edited

    async def delete(
        self, channel: Channel, message_id: UUID, *, user_id: UUID
    ) -> ChannelMessage:
        line = await self._own(channel, message_id, user_id=user_id)
        deleted = await self.lines.delete(line.id)
        if deleted is None:  # pragma: no cover - _own read it
            raise LineRefused("That line is gone", status_code=404)
        return deleted

    async def _own(
        self, channel: Channel, message_id: UUID, *, user_id: UUID
    ) -> ChannelMessage:
        line = await self.lines.get(message_id)
        if line is None or line.channel_id != channel.id:
            raise LineRefused("That line is not in this channel", status_code=404)
        if line.sender.kind is not SenderKind.MEMBER or line.sender.user_id != user_id:
            raise LineRefused("Only who wrote a line may change it", status_code=403)
        return line


async def answer_question(
    uow_factory: UnitOfWorkFactory,
    *,
    channel: Channel,
    message_id: UUID,
    user_id: UUID,
    answer: Mapping[str, object],
) -> ChannelMessage:
    """Answer a question put to this member, through whatever asked it.

    The asking side takes the answer first -- resumes the paused run, submits
    the form -- and only then is the line closed, so a refused answer leaves
    it open to answer again. Only the person it waits on may answer.
    """
    async with uow_factory() as uow:
        line = await LineRepository(uow.session).get(message_id)
    if line is None or line.channel_id != channel.id or line.interaction is None:
        raise LineRefused("There is no question here", status_code=404)
    interaction = line.interaction
    if interaction.awaits != Principal.member(user_id):
        raise LineRefused("This question is somebody else's to answer", status_code=403)
    if not interaction.is_open:
        raise LineRefused("This question is not waiting any more")
    ref = interaction.question.get("ref")
    if isinstance(ref, str) and (handler := answerer(ref)) is not None:
        try:
            await handler(
                Answering(
                    pod_id=channel.pod_id,
                    message=line,
                    ref=ref,
                    answer=answer,
                    user_id=user_id,
                )
            )
        except AnswerRefused as refused:
            raise LineRefused(
                str(refused), status_code=refused.status_code
            ) from refused
    async with uow_factory() as uow:
        answered = await LineRepository(uow.session).answer(
            message_id, answer=answer, by=Principal.member(user_id)
        )
        if answered is not None:
            await announce_closed(uow, answered)
        await uow.commit()
    if answered is not None:
        return answered
    async with uow_factory() as uow:
        # Closed by the asking side as it took the answer: read it as it is.
        current = await LineRepository(uow.session).get(message_id)
    if current is None:  # pragma: no cover - it was read above
        raise LineRefused("That line is gone", status_code=404)
    return current
