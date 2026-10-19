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
from app.modules.channels.domain.access import Viewer, may_see
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Principal,
    Sender,
    SenderKind,
)
from app.modules.channels.domain.parts import MAX_PARTS, MentionPart, Part
from app.modules.channels.infrastructure.lines import LineRepository, NewLine
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.source_repository import SourceRepository
from app.modules.channels.services.posting import Post, Timeline
from app.modules.pod.contracts.members import pod_member_holds, pod_member_id

#: ``contact.read``: who may see the pod's contacts, and so their channels.
_CONTACT_READ = "contact.read"


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
        reply_to = await self._in_channel(channel, post.reply_to_id)
        people, agents = await self._named(channel, post.parts)
        source_id = await self._where_it_goes(channel, post, root)
        message = await Timeline(self.uow).post(
            channel,
            Post(
                line=NewLine(
                    sender=Sender.member(user_id, display_name=display_name),
                    text=post.text,
                    source_id=source_id,
                    members_only=post.members_only,
                    parts=tuple(post.parts),
                    client_message_id=post.client_message_id,
                    thread_root_id=root.id if root else None,
                    reply_to_id=reply_to,
                ),
                mentions=people,
                mentioned_agents=agents,
                deliver_to=[source_id] if source_id else [],
                pod_agent_id=effective_agent_id(None, pod_id=channel.pod_id),
            ),
        )
        if message is None:
            raise LineRefused("Say something first", status_code=422)
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
        if root is not None and root.source_id is not None:
            if post.source_id not in (None, root.source_id):
                raise LineRefused("A reply goes where its thread is", status_code=422)
            return root.source_id
        sources = await self.sources.of_channel(channel.id)
        if post.source_id is not None:
            if all(source.id != post.source_id for source in sources):
                raise LineRefused(
                    "That place does not feed this channel", status_code=404
                )
            return post.source_id
        if channel.kind is ChannelKind.DIRECT:
            # Writing in one's own channel is talking to the pod in Lemma.
            return None
        heard = [
            source
            for source in sources
            if not source.is_pending and source.last_inbound_at is not None
        ]
        return heard[0].id if heard else None

    async def edit(
        self,
        channel: Channel,
        message_id: UUID,
        *,
        user_id: UUID,
        text: str,
        parts: Sequence[Part] | None = None,
    ) -> ChannelMessage:
        line = await self._own(channel, message_id, user_id=user_id)
        body = text.strip()
        if not body:
            raise LineRefused("Say something, or delete it", status_code=422)
        if parts is not None:
            # An edit names people by the same rule a post does.
            await self._named(channel, parts)
        edited = await self.lines.edit(line.id, text=body, parts=parts)
        if edited is None:
            raise LineRefused("That line was deleted")
        return edited

    async def delete(
        self, channel: Channel, message_id: UUID, *, user_id: UUID
    ) -> ChannelMessage:
        line = await self._own(channel, message_id, user_id=user_id)
        deleted = await self.lines.delete(line.id)
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
        if await pod_member_id(self.uow, channel.pod_id, user_id) is None:
            return False
        viewer = Viewer(
            user_id=user_id,
            pod_id=channel.pod_id,
            sees_contacts=await pod_member_holds(
                self.uow, channel.pod_id, user_id, _CONTACT_READ
            ),
        )
        is_member = await self.members.of_user(channel.id, user_id) is not None
        return may_see(channel, viewer, is_member=is_member)
