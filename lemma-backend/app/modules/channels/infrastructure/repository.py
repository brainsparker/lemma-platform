"""Channels: made, found, changed, listed for whoever is looking."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ContactStatus,
    MemberRole,
    NotifyLevel,
    Visibility,
)
from app.modules.channels.infrastructure.access import visible_to
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelModel,
)
from app.modules.channels.infrastructure.revisions import touch_channel
from app.modules.channels.infrastructure.rows import MAX_LINES, channel_entity, now

#: What a caller may change on a channel through ``change``.
CHANGEABLE = frozenset(
    {
        "title",
        "topic",
        "visibility",
        "archived_at",
        "agents_answer",
        "hand_back_after_hours",
        "status",
    }
)


class ChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, channel_id: UUID) -> Channel | None:
        row = await self.session.get(ChannelModel, channel_id)
        return channel_entity(row) if row else None

    async def by_ids(self, channel_ids: Collection[UUID]) -> dict[UUID, Channel]:
        if not channel_ids:
            return {}
        rows = await self.session.scalars(
            select(ChannelModel).where(
                ChannelModel.id.in_(sorted(channel_ids)[:MAX_LINES])
            )
        )
        return {row.id: channel_entity(row) for row in rows}

    async def in_pod(self, *, pod_id: UUID, channel_id: UUID) -> Channel | None:
        row = await self.session.scalar(
            select(ChannelModel).where(
                ChannelModel.id == channel_id, ChannelModel.pod_id == pod_id
            )
        )
        return channel_entity(row) if row else None

    async def lock(self, channel_id: UUID) -> ChannelModel | None:
        """The channel's row, held until the transaction ends: the timeline's
        next number is taken under it."""
        return await self.session.scalar(
            select(ChannelModel).where(ChannelModel.id == channel_id).with_for_update()
        )

    # -- the channels a person has --------------------------------------------

    async def direct(self, *, pod_id: UUID, user_id: UUID) -> Channel:
        """The member's own channel in the pod, made with them in it."""
        channel = await self._one_of_a_kind(
            ChannelModel.pod_id == pod_id,
            ChannelModel.kind == ChannelKind.DIRECT.value,
            ChannelModel.member_user_id == user_id,
            make=lambda: ChannelModel(
                pod_id=pod_id,
                kind=ChannelKind.DIRECT.value,
                visibility=Visibility.PRIVATE.value,
                member_user_id=user_id,
            ),
        )
        await self._seat(channel, user_id=user_id, role=MemberRole.OWNER)
        return channel

    async def for_contact(self, *, pod_id: UUID, contact_id: UUID) -> Channel:
        """The contact's channel in the pod, made with them in it."""
        channel = await self._one_of_a_kind(
            ChannelModel.kind == ChannelKind.CONTACT.value,
            ChannelModel.contact_id == contact_id,
            make=lambda: ChannelModel(
                pod_id=pod_id,
                kind=ChannelKind.CONTACT.value,
                visibility=Visibility.PRIVATE.value,
                contact_id=contact_id,
            ),
        )
        await self._seat(channel, contact_id=contact_id, role=MemberRole.GUEST)
        return channel

    async def open_group(
        self,
        *,
        pod_id: UUID,
        title: str | None,
        visibility: Visibility,
        topic: str | None = None,
        created_by_user_id: UUID | None = None,
    ) -> Channel:
        return await self._open_group(
            ChannelModel(
                pod_id=pod_id,
                kind=ChannelKind.GROUP.value,
                visibility=visibility.value,
                title=title,
                topic=topic,
                created_by_user_id=created_by_user_id,
            )
        )

    async def open_group_once(
        self,
        *,
        channel_id: UUID,
        pod_id: UUID,
        title: str | None,
        visibility: Visibility,
        topic: str | None = None,
        created_by_user_id: UUID | None = None,
    ) -> tuple[Channel, bool]:
        """A group with this id: made now (``True``), or the one a retry of
        the same request already made (``False``)."""
        found = await self.get(channel_id)
        if found is not None:
            return found, False
        try:
            async with self.session.begin_nested():
                made = await self._open_group(
                    ChannelModel(
                        id=channel_id,
                        pod_id=pod_id,
                        kind=ChannelKind.GROUP.value,
                        visibility=visibility.value,
                        title=title,
                        topic=topic,
                        created_by_user_id=created_by_user_id,
                    )
                )
            return made, True
        except IntegrityError:
            winner = await self.get(channel_id)
            if winner is None:
                raise
            return winner, False

    async def _open_group(self, row: ChannelModel) -> Channel:
        self.session.add(row)
        await self.session.flush()
        channel = channel_entity(row)
        if row.created_by_user_id is not None:
            await self._seat(
                channel, user_id=row.created_by_user_id, role=MemberRole.OWNER
            )
        return channel

    async def _one_of_a_kind(
        self, *where: ColumnElement[bool], make: Callable[[], ChannelModel]
    ) -> Channel:
        statement = select(ChannelModel).where(*where)
        found = await self.session.scalar(statement)
        if found is not None:
            return channel_entity(found)
        try:
            async with self.session.begin_nested():
                row = make()
                self.session.add(row)
                await self.session.flush()
                return channel_entity(row)
        except IntegrityError:
            winner = await self.session.scalar(statement)
            if winner is None:
                raise
            return channel_entity(winner)

    async def _seat(
        self,
        channel: Channel,
        *,
        role: MemberRole,
        user_id: UUID | None = None,
        contact_id: UUID | None = None,
    ) -> None:
        await self.session.execute(
            insert(ChannelMemberModel)
            .values(
                channel_id=channel.id,
                pod_id=channel.pod_id,
                user_id=user_id,
                contact_id=contact_id,
                role=role.value,
                notify=NotifyLevel.default_in(channel.kind).value,
            )
            .on_conflict_do_nothing()
        )

    # -- changing one ---------------------------------------------------------

    async def change(self, channel_id: UUID, values: Mapping[str, object]) -> None:
        """Set exactly these fields; a caller decides absent from cleared."""
        unknown = set(values) - CHANGEABLE
        if unknown:
            raise ValueError(f"Not a channel field: {sorted(unknown)}")
        if values:
            closing = values.get("status") == ContactStatus.CLOSED.value
            await touch_channel(self.session, channel_id)
            await self.session.execute(
                update(ChannelModel)
                .where(ChannelModel.id == channel_id)
                .values(
                    **values,
                    # Closing ends the open period its acknowledgement was for.
                    **({"acknowledged_at": None} if closing else {}),
                    updated_at=now(),
                )
            )

    async def assign(self, channel_id: UUID, user_id: UUID | None) -> None:
        """Somebody answers this contact by hand now, or nobody does. Being
        assigned answers a call for a person, and opens the conversation."""
        values: dict[str, object] = {
            "assignee_user_id": user_id,
            "assigned_at": now() if user_id else None,
            "assignee_last_line_at": None,
        }
        if user_id is not None:
            values |= {
                "attention_at": None,
                "attention_reason": None,
                "status": ContactStatus.OPEN.value,
            }
        await touch_channel(self.session, channel_id)
        await self.session.execute(
            update(ChannelModel).where(ChannelModel.id == channel_id).values(**values)
        )

    async def assign_if_free(self, channel_id: UUID, user_id: UUID) -> bool:
        """Assign ``user_id`` only while nobody is: of two members writing to
        an unanswered contact at once, the first takes it. Whether they did."""
        c = ChannelModel
        taken = await self.session.scalar(
            update(c)
            .where(c.id == channel_id, c.assignee_user_id.is_(None))
            .values(
                assignee_user_id=user_id,
                assigned_at=now(),
                assignee_last_line_at=None,
                attention_at=None,
                attention_reason=None,
                status=ContactStatus.OPEN.value,
            )
            .returning(c.id)
        )
        if taken is None:
            return False
        await touch_channel(self.session, channel_id)
        return True

    async def hand_back(
        self, channel_id: UUID, *, user_id: UUID, quiet_since: datetime
    ) -> bool:
        """Let go of an assignee who has not written since ``quiet_since``
        (counted from being assigned until they first write), in one
        conditional write: a member writing meanwhile keeps it, and of two
        turns letting go at once only one does. Whether this one did."""
        c = ChannelModel
        let_go = await self.session.scalar(
            update(c)
            .where(
                c.id == channel_id,
                c.assignee_user_id == user_id,
                func.coalesce(c.assignee_last_line_at, c.assigned_at) < quiet_since,
            )
            .values(assignee_user_id=None, assigned_at=None, assignee_last_line_at=None)
            .returning(c.id)
        )
        if let_go is None:
            return False
        await touch_channel(self.session, channel_id)
        return True

    async def note_assignee_wrote(
        self, channel_id: UUID, *, at: datetime, user_id: UUID | None = None
    ) -> None:
        """The assignee wrote to the contact: the hand-back window runs from
        now. With ``user_id``, only while they are the assignee."""
        where = [ChannelModel.id == channel_id]
        if user_id is not None:
            where.append(ChannelModel.assignee_user_id == user_id)
        await self.session.execute(
            update(ChannelModel)
            .where(*where)
            .values(
                assignee_last_line_at=func.greatest(
                    func.coalesce(ChannelModel.assignee_last_line_at, at), at
                )
            )
        )

    async def acknowledge(self, channel_id: UUID) -> bool:
        """Claim the contact's one "a person will reply" of this open period:
        whether this caller is the one to say it. Conditional, so two
        messages arriving together say it once."""
        claimed = await self.session.scalar(
            update(ChannelModel)
            .where(
                ChannelModel.id == channel_id,
                ChannelModel.acknowledged_at.is_(None),
            )
            .values(acknowledged_at=now())
            .returning(ChannelModel.id)
        )
        return claimed is not None

    async def end_open_period(self, channel_id: UUID) -> None:
        """A member wrote: the next wait is a new one, acknowledged afresh."""
        await self.session.execute(
            update(ChannelModel)
            .where(
                ChannelModel.id == channel_id,
                ChannelModel.acknowledged_at.is_not(None),
            )
            .values(acknowledged_at=None)
        )

    async def call_for_person(self, channel_id: UUID, *, reason: str | None) -> None:
        """The channel asks for a person, until somebody is assigned."""
        await touch_channel(self.session, channel_id)
        await self.session.execute(
            update(ChannelModel)
            .where(ChannelModel.id == channel_id, ChannelModel.attention_at.is_(None))
            .values(
                attention_at=now(),
                attention_reason=(reason or None) and reason[:255],
                status=ContactStatus.OPEN.value,
            )
        )

    async def answer_call(self, channel_id: UUID) -> None:
        """A member spoke where a person was asked for: the call is answered."""
        await self.session.execute(
            update(ChannelModel)
            .where(
                ChannelModel.id == channel_id, ChannelModel.attention_at.is_not(None)
            )
            .values(attention_at=None, attention_reason=None)
        )

    async def delete(self, channel_id: UUID) -> None:
        # Its live subscribers hear, and find it gone.
        await touch_channel(self.session, channel_id)
        await self.session.execute(
            delete(ChannelModel).where(ChannelModel.id == channel_id)
        )

    # -- listing ----------------------------------------------------------------

    async def list_for(
        self,
        viewer: Viewer,
        *,
        kind: ChannelKind | None = None,
        visibility: Visibility | None = None,
        titled: str | None = None,
        archived: bool = False,
        contact_id: UUID | None = None,
        assignee_user_id: UUID | None = None,
        unassigned: bool = False,
        status: ContactStatus | None = None,
        needs_person: bool = False,
        joined_only: bool = False,
        channel_ids: Collection[UUID] | None = None,
        every_group: bool = False,
        limit: int,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[Channel]:
        """The channels ``viewer`` may see, most recently active first --
        among ``channel_ids``, when given. ``titled``: whose title holds these
        words, in any case. ``every_group``: the pod's private groups too, seen
        or not (a pod admin's view of a bot's groups)."""
        if channel_ids is not None and not channel_ids:
            return []
        channel = ChannelModel
        shown = (
            or_(
                visible_to(viewer),
                and_(
                    channel.pod_id == viewer.pod_id,
                    channel.kind == ChannelKind.GROUP.value,
                ),
            )
            if every_group
            else visible_to(viewer)
        )
        statement = select(channel).where(
            shown,
            channel.archived_at.is_not(None)
            if archived
            else channel.archived_at.is_(None),
        )
        statement = statement.where(
            *_fields_match(
                kind=kind,
                visibility=visibility,
                titled=titled,
                contact_id=contact_id,
                assignee_user_id=assignee_user_id,
                unassigned=unassigned,
                status=status,
            )
        )
        if channel_ids is not None:
            statement = statement.where(channel.id.in_(list(channel_ids)))
        if needs_person:
            # A contact waiting on somebody: open, nobody on it, and either
            # the agent is off or it asked for a person.
            statement = statement.where(
                channel.kind == ChannelKind.CONTACT.value,
                channel.status == ContactStatus.OPEN.value,
                channel.assignee_user_id.is_(None),
                or_(
                    channel.attention_at.is_not(None), channel.agents_answer.is_(False)
                ),
            )
        if joined_only:
            member = ChannelMemberModel
            statement = statement.where(
                channel.id.in_(
                    select(member.channel_id).where(member.user_id == viewer.user_id)
                )
            )
        if before is not None:
            # A row comparison, so the page is a range of
            # ``ix_channels_pod_activity`` walked from the cursor; the same
            # test spelled with ``OR`` reads every channel older than it.
            at, channel_id = before
            statement = statement.where(
                tuple_(channel.last_line_at, channel.id) < tuple_(at, channel_id)
            )
        rows = await self.session.scalars(
            statement.order_by(channel.last_line_at.desc(), channel.id.desc()).limit(
                max(1, min(limit, MAX_LINES))
            )
        )
        return [channel_entity(row) for row in rows]

    async def of_member_left(self, *, pod_id: UUID, user_id: UUID) -> list[Channel]:
        """What a member who left the pod still holds: assignments."""
        rows = await self.session.scalars(
            select(ChannelModel)
            .where(
                ChannelModel.pod_id == pod_id,
                ChannelModel.assignee_user_id == user_id,
            )
            .limit(MAX_LINES)
        )
        return [channel_entity(row) for row in rows]


def _like_literal(words: str) -> str:
    """``words`` matched as typed: LIKE's own wildcards are escaped."""
    return words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fields_match(
    *,
    kind: ChannelKind | None,
    visibility: Visibility | None,
    titled: str | None,
    contact_id: UUID | None,
    assignee_user_id: UUID | None,
    unassigned: bool,
    status: ContactStatus | None,
) -> list[ColumnElement[bool]]:
    """A channel list's narrowing by the channel's own fields; none for a
    field not asked about."""
    channel = ChannelModel
    conditions: list[ColumnElement[bool]] = []
    if kind is not None:
        conditions.append(channel.kind == kind.value)
    if visibility is not None:
        conditions.append(channel.visibility == visibility.value)
    if titled is not None and titled.strip():
        words = _like_literal(titled.strip())
        conditions.append(channel.title.ilike(f"%{words}%", escape="\\"))
    if contact_id is not None:
        conditions.append(channel.contact_id == contact_id)
    if assignee_user_id is not None:
        conditions.append(channel.assignee_user_id == assignee_user_id)
    if unassigned:
        conditions.append(channel.assignee_user_id.is_(None))
    if status is not None:
        conditions.append(channel.status == status.value)
    return conditions
