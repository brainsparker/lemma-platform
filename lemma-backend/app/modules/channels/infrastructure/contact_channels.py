"""What a contact's merge or forgetting does to the channels.

**A merge** folds the merged contact's channel into the survivor's: its lines
are numbered on after the survivor's, and everything keyed by the channel or a
line's number moves with them. Both channel rows are locked first, in id order
-- the order every writer that takes two channel locks takes them in -- so a
line written to either while the fold runs either lands before it and is moved,
or waits and finds its channel gone. The merged channel is dropped only after
the conversations working in it have moved (``drop``), since dropping it
unplaces them.

**Forgetting** removes the contact's own channel -- the pod's conversation with
them, every line in it about them -- and, everywhere else, empties their lines
in place: the line keeps its number and its thread, said by nobody, so the
members' replies and the agents' answers around it stay. The answers an agent
gave them keep their words but no longer say whom they were for.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.channels.domain.entities import (
    ChannelKind,
    ContactStatus,
    DeliveryStatus,
)
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMentionModel,
    ChannelMessageDeliveryModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
    ChannelSourceModel,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.channels.infrastructure.revisions import next_rev
from app.modules.channels.infrastructure.rows import now

#: What a member reads on a send of a line whose writer was forgotten.
FORGOTTEN = "The person who wrote this was forgotten, so it was not sent."

_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class Fold:
    """A merged contact's channel, emptied into the survivor's."""

    merged_channel_id: UUID
    survivor_channel_id: UUID


class ContactChannels:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- a contact who turned out to be another ----------------------------------

    async def fold(
        self, *, pod_id: UUID, merged_id: UUID, survivor_id: UUID
    ) -> Fold | None:
        """Move the merged contact's channel into the survivor's; ``None`` when
        the merged contact had none. ``survivor_id`` must be the current
        survivor, not one since merged away."""
        merged_channel = await self._channel_of(pod_id, merged_id)
        if merged_channel is None:
            await self._rename_contact(merged_id=merged_id, survivor_id=survivor_id)
            return None
        survivor_channel = await ChannelRepository(self.session).for_contact(
            pod_id=pod_id, contact_id=survivor_id
        )
        locked = await self._lock(merged_channel, survivor_channel.id)
        await self._rename_contact(merged_id=merged_id, survivor_id=survivor_id)
        merged, head = locked.get(merged_channel), locked.get(survivor_channel.id)
        if merged is None or head is None:
            return None
        base = await self._last_seq(head)
        await self._free_client_ids(merged.id, head.id)
        # The survivor's subscribers are sent the lines and fields it gained;
        # the merged channel's find it gone when it is dropped.
        rev = await next_rev(self.session, head.id, channel_changed=True)
        moved = await self._move_lines(merged.id, head.id, base, rev or 0)
        await self._move_keyed_rows(merged.id, head.id)
        await self._move_seats(merged.id, head.id)
        _settle(head, merged, survivor_had_lines=base > 0)
        head.last_seq = base + moved
        await self.session.flush()
        return Fold(merged_channel_id=merged.id, survivor_channel_id=head.id)

    async def drop(self, fold: Fold) -> None:
        """The folded channel, empty now, goes."""
        await ChannelRepository(self.session).delete(fold.merged_channel_id)

    async def _channel_of(self, pod_id: UUID, contact_id: UUID) -> UUID | None:
        return await self.session.scalar(
            select(ChannelModel.id).where(
                ChannelModel.pod_id == pod_id,
                ChannelModel.kind == ChannelKind.CONTACT.value,
                ChannelModel.contact_id == contact_id,
            )
        )

    async def _lock(self, *channel_ids: UUID) -> dict[UUID, ChannelModel]:
        rows = await self.session.scalars(
            select(ChannelModel)
            .where(ChannelModel.id.in_(channel_ids))
            .order_by(ChannelModel.id)
            .with_for_update()
            # Read again under the lock: a line committed since the row was
            # first loaded moved its head.
            .execution_options(populate_existing=True)
        )
        return {row.id: row for row in rows}

    async def _last_seq(self, head: ChannelModel) -> int:
        """The survivor's newest number, from its lines as well as its head:
        the moved lines go after whichever is higher."""
        highest = await self.session.scalar(
            select(func.max(ChannelMessageModel.seq)).where(
                ChannelMessageModel.channel_id == head.id
            )
        )
        return max(head.last_seq, int(highest or 0))

    async def _free_client_ids(self, merged_id: UUID, survivor_id: UUID) -> None:
        """A client id both channels used is one line per channel: the moved
        one is renamed, the same way every time the fold runs."""
        line, theirs = ChannelMessageModel, aliased(ChannelMessageModel)
        await self.session.execute(
            update(line)
            .where(
                line.channel_id == merged_id,
                line.client_message_id.in_(
                    select(theirs.client_message_id).where(
                        theirs.channel_id == survivor_id,
                        theirs.client_message_id.is_not(None),
                    )
                ),
            )
            .values(
                client_message_id=func.concat(
                    "fold:",
                    func.md5(func.concat(str(merged_id), ":", line.client_message_id)),
                )
            )
            .execution_options(synchronize_session=False)
        )

    async def _move_lines(
        self, merged_id: UUID, survivor_id: UUID, base: int, rev: int
    ) -> int:
        line = ChannelMessageModel
        renumbered = (
            select(
                line.id.label("id"),
                (base + func.row_number().over(order_by=line.seq)).label("seq"),
            )
            .where(line.channel_id == merged_id)
            .subquery()
        )
        result = await self.session.execute(
            update(line)
            .where(line.id == renumbered.c.id)
            .values(channel_id=survivor_id, seq=renumbered.c.seq, rev=rev)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def _move_keyed_rows(self, merged_id: UUID, survivor_id: UUID) -> None:
        """What is keyed by the channel follows its lines; a mention takes its
        line's new number, which is what unread mentions are counted by."""
        mention, line = ChannelMentionModel, ChannelMessageModel
        await self.session.execute(
            update(mention)
            .where(mention.channel_id == merged_id, mention.message_id == line.id)
            .values(channel_id=survivor_id, seq=line.seq)
            .execution_options(synchronize_session=False)
        )
        for table in (ChannelSourceModel, ChannelQuestionModel):
            await self.session.execute(
                update(table)
                .where(table.channel_id == merged_id)
                .values(channel_id=survivor_id)
                .execution_options(synchronize_session=False)
            )

    async def _move_seats(self, merged_id: UUID, survivor_id: UUID) -> None:
        """Members and agents seated only in the merged channel keep a seat;
        where both seat them, the survivor's seat stands."""
        seat, there = ChannelMemberModel, aliased(ChannelMemberModel)
        for key in ("user_id", "agent_id"):
            column, other = getattr(seat, key), getattr(there, key)
            await self.session.execute(
                update(seat)
                .where(
                    seat.channel_id == merged_id,
                    column.is_not(None),
                    ~select(there.id)
                    .where(there.channel_id == survivor_id, other == column)
                    .exists(),
                )
                .values(channel_id=survivor_id)
                .execution_options(synchronize_session=False)
            )

    async def _rename_contact(self, *, merged_id: UUID, survivor_id: UUID) -> None:
        """Lines, questions, mentions and seats that name the merged contact
        name the survivor; where both are named, the survivor's stays."""
        message = ChannelMessageModel
        for column in (message.sender_contact_id, message.on_behalf_contact_id):
            await self.session.execute(
                update(message)
                .where(column == merged_id)
                .values({column.key: survivor_id})
                .execution_options(synchronize_session=False)
            )
        question = ChannelQuestionModel
        for column in (question.awaits_contact_id, question.answered_by_contact_id):
            await self.session.execute(
                update(question)
                .where(column == merged_id)
                .values({column.key: survivor_id})
                .execution_options(synchronize_session=False)
            )
        for table, key in (
            (ChannelMentionModel, ChannelMentionModel.message_id),
            (ChannelMemberModel, ChannelMemberModel.channel_id),
        ):
            both = select(key).where(table.contact_id == survivor_id)
            await self.session.execute(
                delete(table)
                .where(table.contact_id == merged_id, key.in_(both))
                .execution_options(synchronize_session=False)
            )
            await self.session.execute(
                update(table)
                .where(table.contact_id == merged_id)
                .values(contact_id=survivor_id)
                .execution_options(synchronize_session=False)
            )

    # -- a contact forgotten -----------------------------------------------------

    async def forget(self, *, pod_id: UUID, contact_ids: Collection[UUID]) -> int:
        """Take these contacts out of the pod's channels, before their rows go.

        Returns how many of their lines elsewhere were emptied. Channels are
        changed in id order, as every writer holding several takes them.
        """
        ids = sorted(set(contact_ids))
        if not ids:
            return 0
        own = await self._own_channels(pod_id, ids)
        spoken = await self._spoken_in(pod_id, ids)
        seated = await self._seated_in(pod_id, ids)
        seat = ChannelMemberModel
        emptied = 0
        channels = ChannelRepository(self.session)
        for channel_id in sorted(own | spoken | seated):
            if channel_id in own:
                await channels.delete(channel_id)
                continue
            rev = await next_rev(
                self.session, channel_id, channel_changed=channel_id in seated
            )
            if channel_id in spoken:
                emptied += await self._empty_lines(channel_id, ids, rev or 0)
        await self.session.execute(
            delete(seat)
            .where(seat.pod_id == pod_id, seat.contact_id.in_(ids))
            .execution_options(synchronize_session=False)
        )
        return emptied

    async def _own_channels(self, pod_id: UUID, ids: list[UUID]) -> set[UUID]:
        return set(
            await self.session.scalars(
                select(ChannelModel.id).where(
                    ChannelModel.pod_id == pod_id,
                    ChannelModel.kind == ChannelKind.CONTACT.value,
                    ChannelModel.contact_id.in_(ids),
                )
            )
        )

    async def _spoken_in(self, pod_id: UUID, ids: list[UUID]) -> set[UUID]:
        """The channels with a line they said, or one an agent said for them."""
        line = ChannelMessageModel
        return set(
            await self.session.scalars(
                select(line.channel_id)
                .where(
                    line.pod_id == pod_id,
                    line.sender_contact_id.in_(ids)
                    | line.on_behalf_contact_id.in_(ids),
                )
                .distinct()
            )
        )

    async def _seated_in(self, pod_id: UUID, ids: list[UUID]) -> set[UUID]:
        seat = ChannelMemberModel
        return set(
            await self.session.scalars(
                select(seat.channel_id)
                .where(seat.pod_id == pod_id, seat.contact_id.in_(ids))
                .distinct()
            )
        )

    async def _empty_lines(self, channel_id: UUID, ids: list[UUID], rev: int) -> int:
        """Their lines keep their place, said by nobody; an agent's answer to
        them keeps its words and names nobody it was for."""
        line = ChannelMessageModel
        said = await self.session.scalars(
            update(line)
            .where(line.channel_id == channel_id, line.sender_contact_id.in_(ids))
            .values(
                body="",
                parts=[],
                reply_ref=None,
                sender_contact_id=None,
                sender_name=None,
                external_message_id=None,
                deleted_at=func.coalesce(line.deleted_at, now()),
                rev=rev,
            )
            .returning(line.id)
            .execution_options(synchronize_session=False)
        )
        emptied = list(said)
        await self.session.execute(
            update(line)
            .where(line.channel_id == channel_id, line.on_behalf_contact_id.in_(ids))
            .values(on_behalf_contact_id=None, rev=rev)
            .execution_options(synchronize_session=False)
        )
        if emptied:
            d = ChannelMessageDeliveryModel
            await self.session.execute(
                update(d)
                .where(
                    d.message_id.in_(emptied),
                    d.status.in_(
                        (DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value)
                    ),
                    d.claimed_at.is_(None),
                )
                .values(
                    status=DeliveryStatus.SKIPPED.value,
                    reason=FORGOTTEN,
                    next_attempt_at=None,
                    updated_at=now(),
                )
                .execution_options(synchronize_session=False)
            )
        return len(emptied)


def _settle(
    head: ChannelModel, merged: ChannelModel, *, survivor_had_lines: bool
) -> None:
    """The survivor's channel takes the merged one's state where it says more:
    the more recent assignee, open if either is, agents answering only if both
    let them, a call for a person if either made one, and the newer last line.
    """
    if merged.assignee_user_id is not None and (
        head.assignee_user_id is None
        or (merged.assigned_at or _EARLIEST) > (head.assigned_at or _EARLIEST)
    ):
        head.assignee_user_id = merged.assignee_user_id
        head.assigned_at = merged.assigned_at
        head.assignee_last_line_at = merged.assignee_last_line_at
    head.status = _either(head.status, merged.status)
    head.agents_answer = head.agents_answer and merged.agents_answer
    if head.attention_at is None and merged.attention_at is not None:
        head.attention_at = merged.attention_at
        head.attention_reason = merged.attention_reason
    if merged.last_line_id is not None and (
        not survivor_had_lines or merged.last_line_at > head.last_line_at
    ):
        head.last_line_id = merged.last_line_id
        head.last_line_at = merged.last_line_at


def _either(first: str, second: str) -> str:
    for status in (ContactStatus.OPEN, ContactStatus.WAITING_ON_CONTACT):
        if status.value in (first, second):
            return status.value
    return ContactStatus.CLOSED.value


__all__ = ["FORGOTTEN", "ContactChannels", "Fold"]
