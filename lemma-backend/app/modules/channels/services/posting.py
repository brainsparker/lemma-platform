"""Adding a line to a timeline, and everything that follows from it, at once.

``Timeline.post`` is the only way a line is written. In one transaction it
numbers the line, seats whoever said it, records who it names and what it
asks, moves a contact's channel along (a member writing steps in), plans its
deliveries -- to the outside places it is for, and pushes to the members who
hear of it -- and announces it. Nothing that writes a line decides any of
that for itself, so every producer -- a member, an agent, a workflow, a
platform -- gets the same treatment.

Pushes go to a member, outside the app, for: a line naming them; a question
waiting on them; a line in their own channel they did not write; a contact's
line in a channel assigned to them; and, for a member set to ``ALL``, any
line written in Lemma in a group they are in. Never for an agent's line said
on a platform (seen there) or answering in a thread (seen in the app).
``MENTIONS`` keeps only the first two, and so does a note for members only;
``NONE`` and a mute stop them all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.modules.channels.contracts.facts import (
    ChannelDeliveryDue,
    ChannelMessagePosted,
)
from app.modules.channels.domain.entities import (
    AgentAnswers,
    Channel,
    ChannelKind,
    ChannelMessage,
    ContactStatus,
    Member,
    NotifyLevel,
    Principal,
    Question,
    SenderKind,
)
from app.modules.channels.infrastructure.deliveries import DeliveryRepository
from app.modules.channels.infrastructure.lines import LineRepository, NewLine
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.models import ChannelMentionModel
from app.modules.channels.infrastructure.questions import (
    NewQuestion,
    QuestionRepository,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.rows import now


class ChannelNotFound(LookupError):
    """The channel a line was written for is gone."""


@dataclass(frozen=True, slots=True)
class Post:
    line: NewLine
    question: NewQuestion | None = None
    #: People the line names, already resolved (and allowed to see it).
    mentions: Sequence[Principal] = ()
    #: The pod's agents the line names.
    mentioned_agents: Sequence[UUID] = ()
    #: The outside places it is to be carried to.
    deliver_to: Sequence[UUID] = ()
    #: The pod's own agent, which answers everything in a member's channel.
    pod_agent_id: UUID | None = None


class Timeline:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.session = uow.session
        self.channels = ChannelRepository(uow.session)
        self.lines = LineRepository(uow.session)
        self.members = MemberRepository(uow.session)
        self.questions = QuestionRepository(uow.session)
        self.deliveries = DeliveryRepository(uow.session)

    async def post(self, channel: Channel, post: Post) -> ChannelMessage | None:
        """Write the line -- once, however often asked -- and what follows it.
        ``None`` for a line with nothing in it."""
        line = post.line
        if not line.text.strip() and not line.parts and post.question is None:
            return None
        try:
            message, written = await self.lines.append(channel.id, line)
        except LookupError as gone:
            raise ChannelNotFound(str(gone)) from gone
        question = await self._ask(channel, message, post.question)
        if not written:
            return message.model_copy(update={"question": question or message.question})
        await self._seat_and_read(channel, message)
        await self._name(channel, message, post)
        await self._move_contact(channel, message)
        agents = await self._answering(channel, message, post)
        await self._deliver(channel, message, post, question)
        await facts.emit(
            self.uow,
            ChannelMessagePosted(
                pod_id=channel.pod_id,
                channel_id=channel.id,
                channel_kind=channel.kind,
                message_id=message.id,
                seq=message.seq,
                sender_kind=message.sender.kind,
                sender_user_id=message.sender.user_id,
                source_id=message.source_id,
                members_only=message.members_only,
                thread_root_id=message.thread_root_id,
                answering_agent_ids=tuple(agents),
            ),
        )
        return message.model_copy(update={"question": question})

    async def _ask(
        self, channel: Channel, message: ChannelMessage, new: NewQuestion | None
    ) -> Question | None:
        if new is None:
            return None
        return await self.questions.ask(
            message_id=message.id,
            channel_id=channel.id,
            pod_id=channel.pod_id,
            new=new,
        )

    async def _seat_and_read(self, channel: Channel, message: ChannelMessage) -> None:
        """Whoever said it is in the channel; a member has read their own line.
        Nobody but its owner is ever seated in a member's own channel."""
        speaker = message.sender.principal
        if speaker is None:
            return
        if (
            channel.kind is ChannelKind.DIRECT
            and speaker.user_id != channel.member_user_id
        ):
            return
        await self.members.add_person(channel, speaker)
        if speaker.user_id is not None:
            await self.members.mark_read(
                channel_id=channel.id, user_id=speaker.user_id, seq=message.seq
            )

    async def _name(
        self, channel: Channel, message: ChannelMessage, post: Post
    ) -> None:
        """Record who the line names. Naming somebody never puts them in it."""
        named: list[dict[str, object]] = [
            {"user_id": person.user_id, "contact_id": person.contact_id}
            for person in dict.fromkeys(post.mentions)
        ]
        named += [
            {"agent_id": agent_id} for agent_id in dict.fromkeys(post.mentioned_agents)
        ]
        for row in named:
            await self.session.execute(
                insert(ChannelMentionModel)
                .values(
                    message_id=message.id,
                    channel_id=channel.id,
                    pod_id=channel.pod_id,
                    seq=message.seq,
                    **row,
                )
                .on_conflict_do_nothing()
            )

    async def _move_contact(self, channel: Channel, message: ChannelMessage) -> None:
        """A contact's channel: a member writing to them steps in and waits on
        them; the contact writing reopens it. In a group, a member writing
        answers a call for a person there."""
        sender = message.sender
        if (
            channel.kind is ChannelKind.GROUP
            and channel.attention_at is not None
            and sender.kind is SenderKind.MEMBER
            and not message.members_only
        ):
            await self.channels.answer_call(channel.id)
            return
        if channel.kind is not ChannelKind.CONTACT or message.members_only:
            return
        if sender.kind is SenderKind.MEMBER and sender.user_id is not None:
            if channel.assignee_user_id is None:
                await self.channels.assign(channel.id, sender.user_id)
            if channel.assignee_user_id in (None, sender.user_id):
                await self.channels.note_assignee_wrote(
                    channel.id, at=message.created_at
                )
            await self.channels.change(
                channel.id, {"status": ContactStatus.WAITING_ON_CONTACT.value}
            )
            await self.channels.end_open_period(channel.id)
        elif sender.kind is SenderKind.CONTACT:
            await self.channels.change(channel.id, {"status": ContactStatus.OPEN.value})

    async def _answering(
        self, channel: Channel, message: ChannelMessage, post: Post
    ) -> list[UUID]:
        """The agents that answer a member's line written in Lemma: those it
        names, those in the channel that answer every line, and -- in a
        member's own channel with none -- the pod's agent."""
        sender = message.sender
        if (
            sender.kind is not SenderKind.MEMBER
            or message.source_id is not None
            or message.members_only
        ):
            return []
        answering = list(dict.fromkeys(post.mentioned_agents))
        for member in await self.members.agents(channel.id):
            if (
                member.answers is AgentAnswers.EVERY_LINE
                and member.agent_id is not None
                and member.agent_id not in answering
            ):
                answering.append(member.agent_id)
        if not answering and channel.kind is ChannelKind.DIRECT and post.pod_agent_id:
            answering.append(post.pod_agent_id)
        return answering

    async def _deliver(
        self,
        channel: Channel,
        message: ChannelMessage,
        post: Post,
        question: Question | None,
    ) -> None:
        planned = []
        if not message.members_only:
            for source_id in dict.fromkeys(post.deliver_to):
                planned.append(
                    await self.deliveries.plan(
                        message_id=message.id,
                        pod_id=channel.pod_id,
                        source_id=source_id,
                    )
                )
        for user_id in await self._pushed_to(channel, message, post, question):
            planned.append(
                await self.deliveries.plan(
                    message_id=message.id,
                    pod_id=channel.pod_id,
                    recipient_user_id=user_id,
                )
            )
        for delivery in planned:
            await facts.emit(
                self.uow,
                ChannelDeliveryDue(pod_id=channel.pod_id, delivery_id=delivery.id),
            )

    async def _pushed_to(
        self,
        channel: Channel,
        message: ChannelMessage,
        post: Post,
        question: Question | None,
    ) -> list[UUID]:
        sender_user = message.sender.user_id
        named = {person.user_id for person in post.mentions if person.user_id}
        awaited = question.awaits.user_id if question is not None else None
        at = now()
        pushed: list[UUID] = []
        for member in await self.members.people_to_tell(channel.id):
            user_id = member.user_id
            if user_id is None or user_id == sender_user or _muted(member, at):
                continue
            if _hears(channel, message, member, named=named, awaited=awaited):
                pushed.append(user_id)
        return pushed


def _muted(member: Member, at: datetime) -> bool:
    return member.muted_until is not None and member.muted_until > at


def _hears(
    channel: Channel,
    message: ChannelMessage,
    member: Member,
    *,
    named: set[UUID],
    awaited: UUID | None,
) -> bool:
    """Whether this line reaches this member outside the app."""
    user_id = member.user_id
    if user_id in named or user_id == awaited:
        return True
    if member.notify is NotifyLevel.MENTIONS or message.members_only:
        # A note -- a notice, a member's aside -- waits in the app unless it
        # names them.
        return False
    if message.sender.kind is SenderKind.AGENT and (
        message.source_id is not None or message.thread_root_id is not None
    ):
        # Said on a platform, where it was seen; or an answer in the thread of
        # a line the member is writing in the app.
        return False
    if channel.kind is ChannelKind.DIRECT:
        return channel.member_user_id == user_id
    if channel.kind is ChannelKind.CONTACT:
        return (
            message.sender.kind is SenderKind.CONTACT
            and channel.assignee_user_id == user_id
        )
    return message.source_id is None
