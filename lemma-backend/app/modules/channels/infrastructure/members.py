"""Who is in a channel -- members, contacts, agents -- and how far members read."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live.signals import signal_after_commit

from app.modules.channels.domain.entities import (
    AgentAnswers,
    Channel,
    Member,
    MemberRole,
    NotifyLevel,
    Principal,
)
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelModel,
    ChannelReadStateModel,
)
from app.modules.channels.infrastructure.revisions import (
    read_signal,
    touch_channel,
    touch_channels,
)
from app.modules.channels.infrastructure.rows import MAX_LINES, member_entity, now

#: The most members one channel lists at once.
MAX_MEMBERS = 500


class MemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_person(
        self,
        channel: Channel,
        principal: Principal,
        *,
        role: MemberRole | None = None,
        added_by_user_id: UUID | None = None,
    ) -> None:
        """Put a member or a contact in the channel; nothing if already in it."""
        seated = await self.session.execute(
            insert(ChannelMemberModel)
            .values(
                channel_id=channel.id,
                pod_id=channel.pod_id,
                user_id=principal.user_id,
                contact_id=principal.contact_id,
                role=(
                    role
                    or (MemberRole.GUEST if principal.is_contact else MemberRole.MEMBER)
                ).value,
                notify=NotifyLevel.default_in(channel.kind).value,
                added_by_user_id=added_by_user_id,
            )
            .on_conflict_do_nothing()
            .returning(ChannelMemberModel.id)
        )
        if seated.first() is not None:
            await touch_channel(self.session, channel.id)

    async def add_agent(
        self,
        channel: Channel,
        agent_id: UUID,
        *,
        answers: AgentAnswers,
        added_by_user_id: UUID | None = None,
    ) -> None:
        """Put an agent in the channel, or change when it answers there."""
        await touch_channel(self.session, channel.id)
        await self.session.execute(
            insert(ChannelMemberModel)
            .values(
                channel_id=channel.id,
                pod_id=channel.pod_id,
                agent_id=agent_id,
                role=MemberRole.MEMBER.value,
                answers=answers.value,
                added_by_user_id=added_by_user_id,
            )
            .on_conflict_do_update(
                index_elements=["channel_id", "agent_id"],
                index_where=ChannelMemberModel.agent_id.is_not(None),
                set_={"answers": answers.value},
            )
        )

    async def remove(
        self,
        channel_id: UUID,
        *,
        user_id: UUID | None = None,
        contact_id: UUID | None = None,
        agent_id: UUID | None = None,
    ) -> bool:
        member = ChannelMemberModel
        statement = delete(member).where(member.channel_id == channel_id)
        if user_id is not None:
            statement = statement.where(member.user_id == user_id)
        elif contact_id is not None:
            statement = statement.where(member.contact_id == contact_id)
        elif agent_id is not None:
            statement = statement.where(member.agent_id == agent_id)
        else:
            return False
        # The channel before the seat, the order a new line takes them in.
        await touch_channel(self.session, channel_id)
        result = await self.session.execute(statement.returning(member.id))
        return result.first() is not None

    async def set_role(self, channel_id: UUID, user_id: UUID, role: MemberRole) -> None:
        await touch_channel(self.session, channel_id)
        await self.session.execute(
            update(ChannelMemberModel)
            .where(
                ChannelMemberModel.channel_id == channel_id,
                ChannelMemberModel.user_id == user_id,
            )
            .values(role=role.value, updated_at=now())
        )

    async def set_prefs(
        self,
        channel_id: UUID,
        user_id: UUID,
        values: dict[str, object],
    ) -> bool:
        """Change a member's own notify, notify_via, muted_until; only theirs.
        ``False`` when they are not in the channel."""
        allowed = {"notify", "notify_via", "muted_until"}
        if set(values) - allowed:
            raise ValueError(f"Not a member setting: {sorted(set(values) - allowed)}")
        if not values:
            return await self.of_user(channel_id, user_id) is not None
        result = await self.session.execute(
            update(ChannelMemberModel)
            .where(
                ChannelMemberModel.channel_id == channel_id,
                ChannelMemberModel.user_id == user_id,
            )
            .values(**values, updated_at=now())
            .returning(ChannelMemberModel.id)
        )
        return result.first() is not None

    # -- reading ------------------------------------------------------------------

    async def of_channel(
        self, channel_id: UUID, *, limit: int = MAX_MEMBERS
    ) -> list[Member]:
        rows = await self.session.scalars(
            select(ChannelMemberModel)
            .where(ChannelMemberModel.channel_id == channel_id)
            .order_by(ChannelMemberModel.created_at, ChannelMemberModel.id)
            .limit(max(1, min(limit, MAX_MEMBERS)))
        )
        return [member_entity(row) for row in rows]

    async def page(
        self,
        channel_id: UUID,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None = None,
    ) -> list[Member]:
        """Who is in the channel, in the order they joined, from ``after``
        (a seat's join time and id)."""
        member = ChannelMemberModel
        statement = select(member).where(member.channel_id == channel_id)
        if after is not None:
            joined, seat_id = after
            statement = statement.where(
                tuple_(member.created_at, member.id)
                > tuple_(literal(joined), literal(seat_id))
            )
        rows = await self.session.scalars(
            statement.order_by(member.created_at, member.id).limit(
                max(1, min(limit, MAX_MEMBERS))
            )
        )
        return [member_entity(row) for row in rows]

    async def of_user(self, channel_id: UUID, user_id: UUID) -> Member | None:
        row = await self.session.scalar(
            select(ChannelMemberModel).where(
                ChannelMemberModel.channel_id == channel_id,
                ChannelMemberModel.user_id == user_id,
            )
        )
        return member_entity(row) if row else None

    async def of_principal(
        self, channel_id: UUID, principal: Principal
    ) -> Member | None:
        member = ChannelMemberModel
        column = member.user_id if principal.user_id else member.contact_id
        row = await self.session.scalar(
            select(member).where(
                member.channel_id == channel_id,
                column == (principal.user_id or principal.contact_id),
            )
        )
        return member_entity(row) if row else None

    async def users_in(self, channel_id: UUID, user_ids: Collection[UUID]) -> set[UUID]:
        if not user_ids:
            return set()
        rows = await self.session.scalars(
            select(ChannelMemberModel.user_id).where(
                ChannelMemberModel.channel_id == channel_id,
                ChannelMemberModel.user_id.in_(sorted(user_ids)),
            )
        )
        return {row for row in rows if row is not None}

    async def channels_of(
        self, user_id: UUID, channel_ids: Collection[UUID]
    ) -> set[UUID]:
        """Which of ``channel_ids`` the member is in."""
        if not channel_ids:
            return set()
        rows = await self.session.scalars(
            select(ChannelMemberModel.channel_id).where(
                ChannelMemberModel.user_id == user_id,
                ChannelMemberModel.channel_id.in_(sorted(channel_ids)),
            )
        )
        return set(rows)

    async def agents(self, channel_id: UUID) -> list[Member]:
        rows = await self.session.scalars(
            select(ChannelMemberModel)
            .where(
                ChannelMemberModel.channel_id == channel_id,
                ChannelMemberModel.agent_id.is_not(None),
            )
            .limit(MAX_LINES)
        )
        return [member_entity(row) for row in rows]

    async def people_to_tell(self, channel_id: UUID) -> list[Member]:
        """The members of the pod in the channel who hear of its lines at all."""
        rows = await self.session.scalars(
            select(ChannelMemberModel)
            .where(
                ChannelMemberModel.channel_id == channel_id,
                ChannelMemberModel.user_id.is_not(None),
                ChannelMemberModel.notify != NotifyLevel.NONE.value,
            )
            .limit(MAX_MEMBERS)
        )
        return [member_entity(row) for row in rows]

    async def remove_from_pod(self, *, pod_id: UUID, user_id: UUID) -> None:
        """A member who left the pod is in none of its channels."""
        seats = (
            ChannelMemberModel.pod_id == pod_id,
            ChannelMemberModel.user_id == user_id,
        )
        # The channels first (``revisions``): their subscribers re-check who
        # may still read them.
        await touch_channels(
            self.session, select(ChannelMemberModel.channel_id).where(*seats)
        )
        await self.session.execute(delete(ChannelMemberModel).where(*seats))

    # -- read state ---------------------------------------------------------------

    async def mark_read(self, *, channel_id: UUID, user_id: UUID, seq: int) -> int:
        """They have read up to ``seq``; it never goes back. Returns where they
        are now."""
        state = ChannelReadStateModel
        top = select(ChannelModel.last_seq).where(ChannelModel.id == channel_id)
        upto = func.least(seq, top.scalar_subquery())
        statement = (
            insert(state)
            .values(channel_id=channel_id, user_id=user_id, last_read_seq=upto)
            .on_conflict_do_update(
                index_elements=["user_id", "channel_id"],
                set_={
                    "last_read_seq": func.greatest(state.last_read_seq, upto),
                    "updated_at": func.now(),
                },
            )
            .returning(state.last_read_seq)
        )
        read_seq = int(await self.session.scalar(statement) or 0)
        pod_id = await self.session.scalar(
            select(ChannelModel.pod_id).where(ChannelModel.id == channel_id)
        )
        if pod_id is not None:
            signal_after_commit(self.session, pod_id, read_signal(channel_id, user_id))
        return read_seq

    async def read_seq(self, *, channel_id: UUID, user_id: UUID) -> int:
        found = await self.session.scalar(
            select(ChannelReadStateModel.last_read_seq).where(
                ChannelReadStateModel.channel_id == channel_id,
                ChannelReadStateModel.user_id == user_id,
            )
        )
        return int(found or 0)

    async def read_seqs(
        self, *, channel_ids: Collection[UUID], user_id: UUID
    ) -> dict[UUID, int]:
        if not channel_ids:
            return {}
        rows = await self.session.execute(
            select(
                ChannelReadStateModel.channel_id, ChannelReadStateModel.last_read_seq
            ).where(
                ChannelReadStateModel.user_id == user_id,
                ChannelReadStateModel.channel_id.in_(sorted(channel_ids)),
            )
        )
        return {row[0]: int(row[1]) for row in rows}

    async def remove_platform_seat(
        self, channel_id: UUID, principal: Principal
    ) -> bool:
        """Take out a person the platform says left the place this channel
        mirrors -- when the platform is what put them here (nobody in Lemma
        added them, ``added_by_user_id`` empty). Somebody a member added, or an
        admin who joined on request, is the pod's choice and stays."""
        member = ChannelMemberModel
        who = (
            member.user_id == principal.user_id
            if principal.user_id is not None
            else member.contact_id == principal.contact_id
        )
        seated = await self.session.scalar(
            select(member.id).where(
                member.channel_id == channel_id,
                who,
                member.added_by_user_id.is_(None),
            )
        )
        if seated is None:
            return False
        # The channel before the seat, the order a new line takes them in.
        await touch_channel(self.session, channel_id)
        await self.session.execute(delete(member).where(member.id == seated))
        return True
