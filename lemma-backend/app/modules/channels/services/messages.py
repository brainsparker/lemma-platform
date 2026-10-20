"""A member writing in a channel: posting, replying in a thread, editing, deleting.

Where a member's line goes is decided as it is posted:

* a note to members goes nowhere outside;
* a reply in a thread goes where the thread is -- the root's place, and its
  thread there -- and naming another place is refused;
* otherwise the place the member picks, or the one the channel last heard
  from; a member's own channel is the pod in Lemma, and goes nowhere.

Who a line names is read from its parts. A member named must be able to see
the channel, a contact named must be in it; naming anybody else is refused
rather than quietly giving them a badge for a channel they cannot open.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Principal,
    Sender,
    SenderKind,
    Source,
)
from app.modules.channels.domain.idempotency import keyed_client_id
from app.modules.channels.domain.parts import MAX_PARTS, MentionPart, Part
from app.modules.channels.infrastructure.lines import LineRepository, NewLine
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.mentions import drop_mentions, sync_mentions
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.source_repository import SourceRepository
from app.modules.channels.services.posting import Post, Timeline
from app.modules.channels.services.pushes import push_named
from app.modules.channels.services.viewers import member_sees


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
    parts: Sequence[Part] = field(default_factory=tuple)
    client_message_id: str | None = None


class MemberLines:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.lines = LineRepository(uow.session)
        self.members = MemberRepository(uow.session)
        self.sources = SourceRepository(uow.session)

    async def post(
        self,
        channel: Channel,
        *,
        user_id: UUID,
        post: MemberPost,
        display_name: str | None = None,
    ) -> ChannelMessage:
        if channel.is_archived:
            raise LineRefused("This channel is archived; bring it back to write here")
        if len(post.parts) > MAX_PARTS:
            raise LineRefused(f"At most {MAX_PARTS} parts", status_code=422)
        root = await self._thread_root(channel, post.thread_root_id)
        # The member's own name for the line, in their own namespace: a client
        # can never pick a name the pod's own writers use (``run:``, ``wf:``,
        # ``channel-line:`` ...) nor another member's.
        client_id = keyed_client_id(post.client_message_id, user_id=user_id)
        if client_id is not None:
            earlier = await self.lines.named(channel.id, client_id)
            if earlier is not None:
                return _replayed(earlier, post, root=root, user_id=user_id)
        reply_to = await self._in_channel(channel, post.reply_to_id)
        people, agents = await self._named(channel, post.parts)
        place_id = await self._where_it_goes(channel, post, root)
        message = await Timeline(self.uow).post(
            channel,
            Post(
                line=NewLine(
                    sender=Sender.member(user_id, display_name=display_name),
                    text=post.text,
                    # Written here: carried to the place, never heard there.
                    target_source_id=place_id,
                    members_only=post.members_only,
                    parts=tuple(post.parts),
                    client_message_id=client_id,
                    thread_root_id=root.id if root else None,
                    reply_to_id=reply_to,
                ),
                mentions=people,
                mentioned_agents=agents,
                deliver_to=[place_id] if place_id else [],
                pod_agent_id=effective_agent_id(None, pod_id=channel.pod_id),
            ),
        )
        if message is None:
            raise LineRefused("Say something first", status_code=422)
        if message.sender.kind is not SenderKind.MEMBER or (
            message.sender.user_id != user_id
        ):
            # Somebody else's line under the same name: never theirs to replay.
            raise LineRefused("That client_message_id names somebody else's line")
        return message

    async def _thread_root(
        self, channel: Channel, root_id: UUID | None
    ) -> ChannelMessage | None:
        if root_id is None:
            return None
        root = await self.lines.get(root_id)
        if root is None or root.channel_id != channel.id:
            raise LineRefused("That thread is not in this channel", status_code=404)
        if root.thread_root_id is not None:
            # A reply to a reply joins the thread the first one is in.
            parent = await self.lines.get(root.thread_root_id)
            return parent or root
        return root

    async def _in_channel(
        self, channel: Channel, message_id: UUID | None
    ) -> UUID | None:
        if message_id is None:
            return None
        found = await self.lines.get(message_id)
        if found is None or found.channel_id != channel.id:
            raise LineRefused("That line is not in this channel", status_code=404)
        return found.id

    async def _named(
        self, channel: Channel, parts: Sequence[Part]
    ) -> tuple[list[Principal], list[UUID]]:
        named = await Naming(self.uow).of(channel, parts)
        if named.refused:
            raise LineRefused(
                ", ".join(dict.fromkeys(named.refused))
                + " cannot see this channel. Add them first, or do not name them.",
                status_code=422,
            )
        return named.people, named.agents

    async def _where_it_goes(
        self, channel: Channel, post: MemberPost, root: ChannelMessage | None
    ) -> UUID | None:
        if post.members_only:
            if post.source_id is not None:
                raise LineRefused("A note to members goes nowhere outside")
            return None
        if root is not None and root.place_id is not None:
            if post.source_id not in (None, root.place_id):
                raise LineRefused("A reply goes where its thread is", status_code=422)
            return root.place_id
        sources = await self.sources.of_channel(channel.id)
        if post.source_id is not None:
            if all(source.id != post.source_id for source in sources):
                raise LineRefused(
                    "That place does not feed this channel", status_code=404
                )
            return post.source_id
        return carried_to(channel, sources)

    async def edit(
        self,
        channel: Channel,
        message_id: UUID,
        *,
        user_id: UUID,
        text: str | None,
        parts: Sequence[Part] | None = None,
    ) -> ChannelMessage:
        """``None`` keeps the text, or the parts."""
        line = await self._own(channel, message_id, user_id=user_id)
        body = (line.text if text is None else text).strip()
        if not body:
            raise LineRefused("Say something, or delete it", status_code=422)
        if parts is None:
            # The words change; who it names does not.
            edited = await self.lines.edit(line.id, text=body)
        else:
            # An edit names people by the same rule a post does.
            people, agents = await self._named(channel, parts)
            edited = await rewrite_line(
                self.uow,
                message_id=line.id,
                text=body,
                parts=parts,
                people=people,
                agents=agents,
            )
        if edited is None:
            raise LineRefused("That line was deleted")
        return edited

    async def delete(
        self, channel: Channel, message_id: UUID, *, user_id: UUID
    ) -> ChannelMessage:
        line = await self._own(channel, message_id, user_id=user_id)
        deleted = await erase_line(self.uow, message_id=line.id)
        if deleted is None:
            raise LineRefused("That line was deleted already")
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


async def rewrite_line(
    uow: SqlAlchemyUnitOfWork,
    *,
    message_id: UUID,
    text: str,
    parts: Sequence[Part],
    people: Sequence[Principal],
    agents: Sequence[UUID],
) -> ChannelMessage | None:
    """What a line says now, and who it names: ``people`` and ``agents``, as
    resolved from ``parts`` by ``Naming``. Somebody newly named is counted and
    told, as a post tells them; somebody no longer named stops counting.
    ``None`` when the line is gone or deleted."""
    edited = await LineRepository(uow.session).edit(message_id, text=text, parts=parts)
    if edited is None:
        return None
    change = await sync_mentions(
        uow.session, message=edited, people=people, agents=agents
    )
    if change.added_users:
        channel = await ChannelRepository(uow.session).get(edited.channel_id)
        if channel is not None:
            await push_named(uow, channel, edited, change.added_users)
    return edited


async def erase_line(
    uow: SqlAlchemyUnitOfWork, *, message_id: UUID
) -> ChannelMessage | None:
    """Forget what a line said, and whom it named; it keeps its place.
    ``None`` when it was deleted already."""
    deleted = await LineRepository(uow.session).delete(message_id)
    if deleted is not None:
        await drop_mentions(uow.session, [deleted.id])
    return deleted


def _replayed(
    earlier: ChannelMessage,
    post: MemberPost,
    *,
    root: ChannelMessage | None,
    user_id: UUID,
) -> ChannelMessage:
    """The line a retry of this post already wrote -- or a refusal, when the
    same name now asks for a different line."""
    sender = earlier.sender
    if sender.kind is not SenderKind.MEMBER or sender.user_id != user_id:
        raise LineRefused("That client_message_id names somebody else's line")
    same = (
        earlier.members_only == post.members_only
        and earlier.thread_root_id == (root.id if root else None)
        and post.source_id in (None, earlier.place_id)
        # What it says, unless its writer changed that since.
        and (
            earlier.edited_at is not None
            or earlier.is_deleted
            or earlier.text == post.text
        )
    )
    if not same:
        raise LineRefused(
            "That client_message_id was used for another line; name this one anew",
            status_code=422,
        )
    return earlier


def carried_to(channel: Channel, sources: Sequence[Source]) -> UUID | None:
    """Where a line written in Lemma goes when nobody picks: the place the
    channel last heard a person at (``sources`` most recently heard first).
    A member's own channel is the pod in Lemma, and goes nowhere."""
    if channel.kind is ChannelKind.DIRECT:
        return None
    heard = [
        source
        for source in sources
        if not source.is_pending and source.last_inbound_at is not None
    ]
    return heard[0].id if heard else None


@dataclass(frozen=True, slots=True)
class Named:
    """Who a line's parts name: the people who may be told of it, the agents,
    and the names of anybody it may not name."""

    people: list[Principal]
    agents: list[UUID]
    refused: list[str]


class Naming:
    """The rule for naming somebody in a channel: a member must be able to see
    it, a contact must be in it. A member's line naming anybody else is
    refused; a line heard on a platform keeps its words, and only the people
    who may see the channel are told."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.members = MemberRepository(uow.session)

    async def of(self, channel: Channel, parts: Sequence[Part]) -> Named:
        people: list[Principal] = []
        agents: list[UUID] = []
        refused: list[str] = []
        for part in parts:
            if not isinstance(part, MentionPart):
                continue
            if part.kind == "AGENT":
                agents.append(part.id)
            elif part.kind == "MEMBER":
                if await self._member_sees(channel, part.id):
                    people.append(Principal.member(part.id))
                else:
                    refused.append(part.name or "Somebody")
            else:
                person = Principal.contact(part.id)
                if await self.members.of_principal(channel.id, person) is not None:
                    people.append(person)
                else:
                    refused.append(part.name or "A contact")
        return Named(
            people=list(dict.fromkeys(people)),
            agents=list(dict.fromkeys(agents)),
            refused=refused,
        )

    async def _member_sees(self, channel: Channel, user_id: UUID) -> bool:
        return await member_sees(self.uow, channel, user_id)
