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
line written in Lemma in a group they are in. Never for an agent's line at a
platform (seen there) or answering in a thread (seen in the app), nor for a
line withheld from them (they could not read it). ``MENTIONS`` -- a group
seat's default -- keeps only the first two, and so does a note for members
only; ``NONE`` and a mute stop them all. Somebody named or asked who is not
in the channel is told too, when they may see it. Everything but the first
two is held back from a member who had the app open in the last
``PRESENT_FOR_SECONDS``: they read it there.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.core.live.presence import seen_within
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
from app.modules.channels.infrastructure.source_repository import SourceRepository
from app.modules.channels.domain.projection import withheld_from
from app.modules.channels.infrastructure.rows import now
from app.modules.channels.services.viewers import member_sees

#: A member who had the app open this recently reads a line there: only what
#: names or asks them is pushed.
PRESENT_FOR_SECONDS = 5 * 60


class ChannelNotFound(LookupError):
    """The channel a line was written for is gone."""


class LineMisplaced(ValueError):
    """A line names a thread, a line or a place of another channel."""


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
        self.sources = SourceRepository(uow.session)

    async def post(self, channel: Channel, post: Post) -> ChannelMessage | None:
        """Write the line -- once, however often asked -- and what follows it.
        ``None`` for a line with nothing in it."""
        line = post.line
        if not line.text.strip() and not line.parts and post.question is None:
            return None
        await self._in_place(channel, post)
        try:
            message, written = await self.lines.append(channel.id, line)
        except LookupError as gone:
            raise ChannelNotFound(str(gone)) from gone
        question = await self._ask(channel, message, post.question)
        if not written:
            # Heard again, perhaps now knowing who said it: they are seated.
            await self._seat_and_read(channel, message)
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

    async def _in_place(self, channel: Channel, post: Post) -> None:
        """Everything the line hangs from is of this channel: its thread, the
        line it replies to, the place it was heard at or goes to."""
        line = post.line
        for message_id in {
            each for each in (line.thread_root_id, line.reply_to_id) if each
        }:
            found = await self.lines.get(message_id)
            if found is None or found.channel_id != channel.id:
                raise LineMisplaced(f"Line {message_id} is not in this channel")
        places = {
            each
            for each in (line.source_id, line.target_source_id, *post.deliver_to)
            if each is not None
        }
        if places:
            ours = await self.sources.in_channel(channel.id, places)
            if missing := places - ours:
                raise LineMisplaced(
                    f"{sorted(map(str, missing))} do not feed this channel"
                )

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
                # Conditional: of two members writing to an unanswered
                # contact at once, the first takes it.
                await self.channels.assign_if_free(channel.id, sender.user_id)
            # Theirs only while they are its assignee, read in the same write.
            await self.channels.note_assignee_wrote(
                channel.id, at=message.created_at, user_id=sender.user_id
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
        """The agents that answer a member's line written in Lemma -- wherever
        it is carried: those it names, those in the channel that answer every
        line, and -- in a member's own channel with none -- the pod's agent.
        A line heard on a platform is answered there, by ``agent_surfaces``."""
        sender = message.sender
        if (
            sender.kind is not SenderKind.MEMBER
            or message.heard_outside
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
                ChannelDeliveryDue(
                    pod_id=channel.pod_id,
                    delivery_id=delivery.id,
                    target=delivery.target_key,
                ),
            )

    async def _pushed_to(
        self,
        channel: Channel,
        message: ChannelMessage,
        post: Post,
        question: Question | None,
    ) -> list[UUID]:
        """Who is pushed the line, in order. Everybody told is either *called*
        -- named, or asked -- or hears it by their settings; only the second
        are held back while they have the app open."""
        named = {person.user_id for person in post.mentions if person.user_id}
        awaited = question.awaits.user_id if question is not None else None
        called = named | ({awaited} if awaited else set())
        asked = message.model_copy(update={"question": question})
        heard = await self._seated_who_hear(channel, asked, named, awaited)
        heard.update(
            dict.fromkeys(await self._called_not_seated(channel, asked, called), True)
        )
        in_app = await seen_within(
            channel.pod_id,
            [user_id for user_id, urgent in heard.items() if not urgent],
            seconds=PRESENT_FOR_SECONDS,
        )
        return [
            user_id
            for user_id, urgent in heard.items()
            if urgent or user_id not in in_app
        ]

    async def _seated_who_hear(
        self,
        channel: Channel,
        message: ChannelMessage,
        named: set[UUID],
        awaited: UUID | None,
    ) -> dict[UUID, bool]:
        """The channel's members who hear of the line, each with whether it
        calls them."""
        at = now()
        heard: dict[UUID, bool] = {}
        for member in await self.members.people_to_tell(channel.id):
            user_id = member.user_id
            if user_id is None or user_id == message.sender.user_id:
                continue
            if _muted(member, at) or _withheld(channel, message, user_id):
                continue
            if _hears(channel, message, member, named=named, awaited=awaited):
                heard[user_id] = user_id in named or user_id == awaited
        return heard

    async def _called_not_seated(
        self, channel: Channel, message: ChannelMessage, called: set[UUID]
    ) -> list[UUID]:
        """Who the line names or asks without being in the channel: told, when
        they may see it."""
        seated = await self.members.users_in(channel.id, called)
        told: list[UUID] = []
        for user_id in sorted(called - seated - {message.sender.user_id}):
            if user_id is None or _withheld(channel, message, user_id):
                continue
            if await member_sees(self.uow, channel, user_id):
                told.append(user_id)
        return told


def _muted(member: Member, at: datetime) -> bool:
    return member.muted_until is not None and member.muted_until > at


def _withheld(channel: Channel, message: ChannelMessage, user_id: UUID) -> bool:
    """A line they are shown only as given: never pushed to them in full."""
    return withheld_from(
        message,
        user_id=user_id,
        in_their_direct=channel.kind is ChannelKind.DIRECT
        and channel.member_user_id == user_id,
    )


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
        message.place_id is not None or message.thread_root_id is not None
    ):
        # Said at a platform, where it was seen; or an answer in the thread of
        # a line the member is writing in the app.
        return False
    if channel.kind is ChannelKind.DIRECT:
        return channel.member_user_id == user_id
    if channel.kind is ChannelKind.CONTACT:
        return (
            message.sender.kind is SenderKind.CONTACT
            and channel.assignee_user_id == user_id
        )
    # A group: a line written in Lemma, wherever it is carried. One heard on
    # a platform was seen there.
    return not message.heard_outside
