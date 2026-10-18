"""Reading and writing channels, their sources and people.

A channel's lines are ``lines.LineRepository``'s.

Every find-or-create here runs its insert in a savepoint behind a unique index,
the way contacts open: two first messages from one place arrive together often
enough, both read "nothing yet", and the loser re-reads the winner's row
instead of failing or making a second one.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    NotifyLevel,
    Participant,
    Principal,
    Source,
    SourceKind,
)
from app.modules.channels.infrastructure.rows import (
    MAX_LINES,
    channel_entity,
    now,
    participant_entity,
    source_entity,
)
from app.modules.channels.infrastructure.models import (
    ChannelAgentModel,
    ChannelMentionModel,
    ChannelMessageDeliveryModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelParticipantModel,
    ChannelSourceModel,
)


class ChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- channels -------------------------------------------------------------

    async def get(self, channel_id: UUID) -> Channel | None:
        row = await self.session.get(ChannelModel, channel_id)
        return channel_entity(row) if row else None

    async def direct(self, *, pod_id: UUID, user_id: UUID) -> Channel:
        """The member's DIRECT channel in the pod, made if there is none."""
        return await self._one_of_a_kind(
            ChannelModel.pod_id == pod_id,
            ChannelModel.kind == ChannelKind.DIRECT.value,
            ChannelModel.member_user_id == user_id,
            make=lambda: ChannelModel(
                pod_id=pod_id, kind=ChannelKind.DIRECT.value, member_user_id=user_id
            ),
        )

    async def for_contact(self, *, pod_id: UUID, contact_id: UUID) -> Channel:
        """The contact's CONTACT channel in the pod, made if there is none."""
        return await self._one_of_a_kind(
            ChannelModel.pod_id == pod_id,
            ChannelModel.kind == ChannelKind.CONTACT.value,
            ChannelModel.contact_id == contact_id,
            make=lambda: ChannelModel(
                pod_id=pod_id, kind=ChannelKind.CONTACT.value, contact_id=contact_id
            ),
        )

    async def open_group(
        self,
        *,
        pod_id: UUID,
        title: str | None,
        topic: str | None = None,
        created_by_user_id: UUID | None = None,
    ) -> Channel:
        row = ChannelModel(
            pod_id=pod_id,
            kind=ChannelKind.GROUP.value,
            title=title,
            topic=topic,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return channel_entity(row)

    async def change(
        self,
        channel_id: UUID,
        *,
        title: str | None = None,
        topic: str | None = None,
        archived: bool | None = None,
    ) -> Channel | None:
        """Rename, re-describe or archive a channel; ``None`` leaves a field be."""
        row = await self.session.get(ChannelModel, channel_id)
        if row is None:
            return None
        if title is not None:
            row.title = title
        if topic is not None:
            row.topic = topic or None
        if archived is not None:
            row.archived_at = now() if archived else None
        await self.session.flush()
        return channel_entity(row)

    async def call_for_person(self, channel_id: UUID, *, reason: str | None) -> None:
        """The channel asks for a person, until somebody steps in."""
        await self.session.execute(
            update(ChannelModel)
            .where(ChannelModel.id == channel_id, ChannelModel.attention_at.is_(None))
            .values(
                attention_at=now(), attention_reason=reason[:255] if reason else None
            )
        )

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

    async def set_handler(self, channel_id: UUID, user_id: UUID | None) -> None:
        """Somebody answers by hand now, or nobody does. Stepping in answers a
        call for a person."""
        values: dict[str, object] = {"handler_user_id": user_id}
        if user_id is not None:
            values |= {"attention_at": None, "attention_reason": None}
        await self.session.execute(
            update(ChannelModel).where(ChannelModel.id == channel_id).values(**values)
        )

    async def list_for_member(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        kinds: tuple[ChannelKind, ...],
        before: tuple[datetime, UUID] | None,
        limit: int,
        archived: bool = False,
    ) -> list[Channel]:
        """The channels a member of the pod sees, most recently active first.

        Their own DIRECT channel, and every CONTACT and GROUP channel: the
        pod's contacts and groups are every member's to look after.
        """
        statement = select(ChannelModel).where(
            ChannelModel.pod_id == pod_id,
            ChannelModel.kind.in_([kind.value for kind in kinds]),
            or_(
                ChannelModel.kind != ChannelKind.DIRECT.value,
                ChannelModel.member_user_id == user_id,
            ),
            ChannelModel.archived_at.is_not(None)
            if archived
            else ChannelModel.archived_at.is_(None),
        )
        activity = func.coalesce(ChannelModel.last_message_at, ChannelModel.created_at)
        if before is not None:
            at, channel_id = before
            statement = statement.where(
                or_(activity < at, and_(activity == at, ChannelModel.id < channel_id))
            )
        rows = await self.session.scalars(
            statement.order_by(activity.desc(), ChannelModel.id.desc()).limit(
                max(1, min(limit, MAX_LINES))
            )
        )
        return [channel_entity(row) for row in rows]

    async def attach_agent(self, *, channel_id: UUID, agent_id: UUID) -> None:
        await self.session.execute(
            insert(ChannelAgentModel)
            .values(channel_id=channel_id, agent_id=agent_id)
            .on_conflict_do_nothing()
        )

    async def fold_contact(
        self, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID
    ) -> None:
        """A contact turned out to be another: their channel becomes the survivor's.

        Moved rather than copied -- sources, lines and people -- and the merged
        contact's channel goes once it holds nothing. A conversation that was
        in it loses the pointer: the person's next word continues the
        survivor's conversation there, or places this one when there is none.
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
        for table in (ChannelSourceModel, ChannelMessageModel, ChannelMentionModel):
            await self.session.execute(
                update(table)
                .where(table.channel_id == merged.id)
                .values(channel_id=survivor.id)
            )
        await self.session.execute(
            delete(ChannelParticipantModel).where(
                ChannelParticipantModel.channel_id == merged.id
            )
        )
        await self.join(channel=survivor, principal=Principal.contact(survivor_id))
        await self.session.delete(merged)
        await self.session.flush()

    async def _rename_contact(self, *, merged_id: UUID, survivor_id: UUID) -> None:
        """Lines and seats that name the merged contact name the survivor."""
        await self.session.execute(
            update(ChannelMessageModel)
            .where(ChannelMessageModel.sender_contact_id == merged_id)
            .values(sender_contact_id=survivor_id)
        )
        for column in (
            ChannelMessageModel.on_behalf_contact_id,
            ChannelMessageModel.awaits_contact_id,
            ChannelMessageModel.answered_by_contact_id,
        ):
            await self.session.execute(
                update(ChannelMessageModel)
                .where(column == merged_id)
                .values({column.key: survivor_id})
            )
        await self.session.execute(
            update(ChannelMessageDeliveryModel)
            .where(ChannelMessageDeliveryModel.recipient_contact_id == merged_id)
            .values(recipient_contact_id=survivor_id)
        )
        # A line naming both keeps the survivor's mention.
        named = select(ChannelMentionModel.message_id).where(
            ChannelMentionModel.contact_id == survivor_id
        )
        await self.session.execute(
            delete(ChannelMentionModel).where(
                ChannelMentionModel.contact_id == merged_id,
                ChannelMentionModel.message_id.in_(named),
            )
        )
        await self.session.execute(
            update(ChannelMentionModel)
            .where(ChannelMentionModel.contact_id == merged_id)
            .values(contact_id=survivor_id)
        )
        # A seat both already hold in one channel keeps the survivor's.
        held = select(ChannelParticipantModel.channel_id).where(
            ChannelParticipantModel.contact_id == survivor_id
        )
        await self.session.execute(
            delete(ChannelParticipantModel).where(
                ChannelParticipantModel.contact_id == merged_id,
                ChannelParticipantModel.channel_id.in_(held),
            )
        )
        await self.session.execute(
            update(ChannelParticipantModel)
            .where(ChannelParticipantModel.contact_id == merged_id)
            .values(contact_id=survivor_id)
        )

    # -- sources --------------------------------------------------------------

    async def find_source(
        self,
        *,
        pod_id: UUID,
        surface_id: UUID | None,
        platform: str,
        external_channel_id: str | None,
        external_user_id: str | None,
    ) -> Source | None:
        row = await self.session.scalar(
            select(ChannelSourceModel).where(
                ChannelSourceModel.pod_id == pod_id,
                ChannelSourceModel.surface_id.is_not_distinct_from(surface_id),
                ChannelSourceModel.platform == platform,
                func.coalesce(ChannelSourceModel.external_channel_id, "")
                == (external_channel_id or ""),
                func.coalesce(ChannelSourceModel.external_user_id, "")
                == (external_user_id or ""),
            )
        )
        return source_entity(row) if row else None

    async def add_source(
        self,
        *,
        channel: Channel,
        surface_id: UUID | None,
        platform: str,
        kind: SourceKind,
        external_channel_id: str | None,
        external_user_id: str | None,
        title: str | None,
    ) -> Source | None:
        """The new source, or ``None`` when another writer added this place first."""
        try:
            async with self.session.begin_nested():
                row = ChannelSourceModel(
                    channel_id=channel.id,
                    pod_id=channel.pod_id,
                    surface_id=surface_id,
                    platform=platform,
                    kind=kind.value,
                    external_channel_id=external_channel_id,
                    external_user_id=external_user_id,
                    title=title,
                )
                self.session.add(row)
                await self.session.flush()
                return source_entity(row)
        except IntegrityError:
            return None

    async def get_source(self, source_id: UUID) -> Source | None:
        row = await self.session.get(ChannelSourceModel, source_id)
        return source_entity(row) if row else None

    async def sources_of_many(
        self, channel_ids: list[UUID]
    ) -> dict[UUID, list[Source]]:
        """The sources of each of these channels, in one read."""
        if not channel_ids:
            return {}
        rows = await self.session.scalars(
            select(ChannelSourceModel)
            .where(ChannelSourceModel.channel_id.in_(channel_ids[:MAX_LINES]))
            .order_by(ChannelSourceModel.created_at)
            .limit(MAX_LINES * 4)
        )
        found: dict[UUID, list[Source]] = {}
        for row in rows:
            found.setdefault(row.channel_id, []).append(source_entity(row))
        return found

    async def sources_of(self, channel_id: UUID) -> list[Source]:
        rows = await self.session.scalars(
            select(ChannelSourceModel)
            .where(ChannelSourceModel.channel_id == channel_id)
            .order_by(ChannelSourceModel.created_at)
            .limit(MAX_LINES)
        )
        return [source_entity(row) for row in rows]

    # -- people ---------------------------------------------------------------

    async def join(self, *, channel: Channel, principal: Principal) -> None:
        """Make somebody a participant; nothing when they already are one."""
        await self.session.execute(
            insert(ChannelParticipantModel)
            .values(
                channel_id=channel.id,
                pod_id=channel.pod_id,
                user_id=principal.user_id,
                contact_id=principal.contact_id,
            )
            .on_conflict_do_nothing()
        )

    async def participants(self, channel_id: UUID) -> list[Participant]:
        rows = await self.session.scalars(
            select(ChannelParticipantModel)
            .where(ChannelParticipantModel.channel_id == channel_id)
            .order_by(ChannelParticipantModel.created_at)
            .limit(MAX_LINES)
        )
        return [participant_entity(row) for row in rows]

    async def leave(self, *, channel_id: UUID, principal: Principal) -> None:
        await self.session.execute(
            delete(ChannelParticipantModel).where(
                ChannelParticipantModel.channel_id == channel_id,
                ChannelParticipantModel.user_id.is_not_distinct_from(principal.user_id),
                ChannelParticipantModel.contact_id.is_not_distinct_from(
                    principal.contact_id
                ),
            )
        )

    async def set_notify(
        self,
        *,
        channel: Channel,
        user_id: UUID,
        notify: NotifyLevel,
        notify_via: tuple[str, ...] | None,
    ) -> None:
        """How a member hears of this channel's lines -- joining it to say so."""
        via = list(notify_via) if notify_via is not None else None
        await self.session.execute(
            insert(ChannelParticipantModel)
            .values(
                channel_id=channel.id,
                pod_id=channel.pod_id,
                user_id=user_id,
                notify=notify.value,
                notify_via=via,
            )
            .on_conflict_do_update(
                index_elements=["channel_id", "user_id"],
                index_where=ChannelParticipantModel.user_id.is_not(None),
                set_={"notify": notify.value, "notify_via": via},
            )
        )

    async def participant(
        self, *, channel_id: UUID, principal: Principal
    ) -> Participant | None:
        row = await self.session.scalar(
            select(ChannelParticipantModel).where(
                ChannelParticipantModel.channel_id == channel_id,
                ChannelParticipantModel.user_id.is_not_distinct_from(principal.user_id),
                ChannelParticipantModel.contact_id.is_not_distinct_from(
                    principal.contact_id
                ),
            )
        )
        return participant_entity(row) if row else None

    async def mark_read(self, *, channel: Channel, user_id: UUID) -> None:
        """The member has read the channel up to now -- joining it if they only
        ever read it, so the mark has a row to live on."""
        read_at = now()
        await self.session.execute(
            insert(ChannelParticipantModel)
            .values(
                channel_id=channel.id,
                pod_id=channel.pod_id,
                user_id=user_id,
                last_read_at=read_at,
            )
            .on_conflict_do_update(
                index_elements=["channel_id", "user_id"],
                index_where=ChannelParticipantModel.user_id.is_not(None),
                set_={"last_read_at": read_at},
            )
        )
