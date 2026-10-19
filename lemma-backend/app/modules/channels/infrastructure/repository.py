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
    Visibility,
)
from app.modules.channels.infrastructure.access import visible_to
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMentionModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
    ChannelReadStateModel,
    ChannelSourceModel,
)
from app.modules.channels.infrastructure.revisions import next_rev, touch_channel
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

    async def note_assignee_wrote(self, channel_id: UUID, *, at: datetime) -> None:
        """The assignee wrote to the contact: the hand-back window runs from now."""
        await self.session.execute(
            update(ChannelModel)
            .where(ChannelModel.id == channel_id)
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
        among ``channel_ids``, when given. ``every_group``: the pod's private
        groups too, seen or not (a pod admin's view of a bot's groups)."""
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
        if kind is not None:
            statement = statement.where(channel.kind == kind.value)
        if channel_ids is not None:
            statement = statement.where(channel.id.in_(list(channel_ids)))
        if contact_id is not None:
            statement = statement.where(channel.contact_id == contact_id)
        if assignee_user_id is not None:
            statement = statement.where(channel.assignee_user_id == assignee_user_id)
        if unassigned:
            statement = statement.where(channel.assignee_user_id.is_(None))
        if status is not None:
            statement = statement.where(channel.status == status.value)
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

    # -- a contact who turned out to be another -------------------------------

    async def fold_contact(
        self, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID
    ) -> None:
        """The merged contact's channel becomes the survivor's.

        Its sources and lines move, the lines numbered on after the
        survivor's; the merged channel goes once it holds nothing.
        """
        merged = await self.session.scalar(
            select(ChannelModel).where(
                ChannelModel.pod_id == pod_id,
                ChannelModel.kind == ChannelKind.CONTACT.value,
                ChannelModel.contact_id == merged_id,
            )
        )
        await self._rename_contact(merged_id=merged_id, survivor_id=survivor_id)
        if merged is None:
            return
        survivor = await self.for_contact(pod_id=pod_id, contact_id=survivor_id)
        head = await self.lock(survivor.id)
        assert head is not None
        moved = await self.session.scalar(
            select(func.count()).where(ChannelMessageModel.channel_id == merged.id)
        )
        # The survivor's subscribers are sent the lines it gained; the merged
        # channel's find it gone.
        rev = await next_rev(self.session, survivor.id)
        await touch_channel(self.session, merged.id)
        renumbered = (
            select(
                ChannelMessageModel.id.label("id"),
                (
                    head.last_seq
                    + func.row_number().over(order_by=ChannelMessageModel.seq)
                ).label("seq"),
            )
            .where(ChannelMessageModel.channel_id == merged.id)
            .subquery()
        )
        await self.session.execute(
            update(ChannelMessageModel)
            .where(ChannelMessageModel.id == renumbered.c.id)
            .values(channel_id=survivor.id, seq=renumbered.c.seq, rev=rev or 0)
        )
        for table in (ChannelSourceModel, ChannelMentionModel, ChannelQuestionModel):
            await self.session.execute(
                update(table)
                .where(table.channel_id == merged.id)
                .values(channel_id=survivor.id)
            )
        head.last_seq = head.last_seq + int(moved or 0)
        head.last_line_at = func.greatest(head.last_line_at, merged.last_line_at)
        await self.session.execute(
            delete(ChannelReadStateModel).where(
                ChannelReadStateModel.channel_id == merged.id
            )
        )
        await self.session.delete(merged)
        await self.session.flush()

    async def _rename_contact(self, *, merged_id: UUID, survivor_id: UUID) -> None:
        """Lines, questions, mentions and seats that name the merged contact
        name the survivor; where both are named, the survivor's stays."""
        message = ChannelMessageModel
        for column in (message.sender_contact_id, message.on_behalf_contact_id):
            await self.session.execute(
                update(message)
                .where(column == merged_id)
                .values({column.key: survivor_id})
            )
        question = ChannelQuestionModel
        for column in (question.awaits_contact_id, question.answered_by_contact_id):
            await self.session.execute(
                update(question)
                .where(column == merged_id)
                .values({column.key: survivor_id})
            )
        for table, key in (
            (ChannelMentionModel, ChannelMentionModel.message_id),
            (ChannelMemberModel, ChannelMemberModel.channel_id),
        ):
            both = select(key).where(table.contact_id == survivor_id)
            await self.session.execute(
                delete(table).where(table.contact_id == merged_id, key.in_(both))
            )
            await self.session.execute(
                update(table)
                .where(table.contact_id == merged_id)
                .values(contact_id=survivor_id)
            )
