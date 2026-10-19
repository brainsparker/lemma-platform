"""Deliveries: each send of a line outside Lemma, and the only record of it.

A delivery row is made once per line and target (a place, or a member pushed
to) before anything is sent. A worker claims a due row, sends, and records
the outcome on the same row -- so a retried job finds ``SENT`` and stops,
and a failure is retried with backoff until it is ``DEAD``.

A claim is held for ``CLAIM_LEASE`` and stamps ``claimed_at``; every outcome
clears it. A claim that lapsed with ``claimed_at`` still set is a send nobody
finished -- the sender died somewhere between taking it and recording what
happened, perhaps after the platform took it. It is never sent again blind:
when the platform's id for it was noted before the call (a live stream the
answer closes) it is ``SENT``, since the message is there; where the
platform dedupes a resend by the delivery's key the sender takes it again
(``reclaim``); otherwise it is ``DEAD`` with a reason saying it may or may not
have arrived, and a member can retry it. Saying something twice is worse than
saying, visibly, that it may not have arrived.

Every time here is the database's, as a row is planned by: a sender whose
clock runs behind must not find a row it just planned not yet due. A row
waits while an earlier line's row to the same target has yet to go
(``_behind_an_older_one``), so a place reads a channel in order.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, and_, case, exists, func, or_, select, update
from sqlalchemy.orm import aliased
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import Delivery, DeliveryStatus
from app.modules.channels.infrastructure.models import (
    ChannelMentionModel,
    ChannelMessageDeliveryModel,
    ChannelMessageModel,
    ChannelQuestionModel,
)
from app.modules.channels.infrastructure.revisions import touch_line, touch_lines
from app.modules.channels.infrastructure.rows import MAX_LINES, delivery_entity

#: Tries before a delivery is given up on.
MAX_ATTEMPTS = 6

#: How long a sender holds a claim. Above the sender's own bound on one send
#: (``agent_surfaces`` gives up on a platform at 60 s), so a slow platform is
#: a failure it records, not a lapsed claim.
CLAIM_LEASE = timedelta(seconds=90)

#: What a member reads on a send nobody finished.
INTERRUPTED = (
    "Sending this was interrupted, so it may or may not have arrived. It was "
    "not sent again in case it did."
)

_DUE = (DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value)


def _settleable() -> ColumnElement[bool]:
    """A delivery a sender may still record an outcome on: one it is sending,
    or one settled as interrupted while it was -- the sender's own outcome
    is better than that guess. A platform's report (``undelivered``) and
    every other outcome stand."""
    d = ChannelMessageDeliveryModel
    return or_(
        d.status.in_(_DUE),
        and_(d.status == DeliveryStatus.DEAD.value, d.reason == INTERRUPTED),
    )


def backoff(attempts: int, *, retry_after: float | None = None) -> timedelta:
    """How long after the ``attempts``-th failure to try again: 30 s, 2 min,
    8 min, 32 min, ~2 h -- or longer, when the platform asked for longer."""
    wait = timedelta(seconds=30 * 4 ** max(0, attempts - 1))
    if retry_after is not None and retry_after > 0:
        return max(wait, timedelta(seconds=retry_after))
    return wait


def _behind_an_older_one() -> ColumnElement[bool]:
    """A delivery with an older one to the same target still to go.

    A channel's lines reach one place -- and one member's pushes -- in the
    order they were written: a row waits while a row for an earlier line of
    the same channel, to the same target, is pending, being sent, or failed
    and waiting out its backoff (a ``Retry-After`` holds back everything
    behind it). Lines of other channels are not held: a member's pushes from
    one conversation do not wait on another's.
    """
    d = ChannelMessageDeliveryModel
    older = aliased(ChannelMessageDeliveryModel)
    line, older_line = aliased(ChannelMessageModel), aliased(ChannelMessageModel)
    same_target = or_(
        and_(d.source_id.is_not(None), older.source_id == d.source_id),
        and_(
            d.recipient_user_id.is_not(None),
            older.recipient_user_id == d.recipient_user_id,
        ),
    )
    return exists().where(
        older.id != d.id,
        older.pod_id == d.pod_id,
        older.status.in_(_DUE),
        same_target,
        line.id == d.message_id,
        older_line.id == older.message_id,
        older_line.channel_id == line.channel_id,
        older_line.seq < line.seq,
    )


class DeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def plan(
        self,
        *,
        message_id: UUID,
        pod_id: UUID,
        source_id: UUID | None = None,
        recipient_user_id: UUID | None = None,
    ) -> Delivery:
        """The line's delivery to this target, made once; a second plan finds it."""
        d = ChannelMessageDeliveryModel
        await self.session.execute(
            insert(d)
            .values(
                message_id=message_id,
                pod_id=pod_id,
                source_id=source_id,
                recipient_user_id=recipient_user_id,
                status=DeliveryStatus.PENDING.value,
                next_attempt_at=func.now(),
            )
            .on_conflict_do_nothing()
        )
        target = d.source_id if source_id is not None else d.recipient_user_id
        row = await self.session.scalar(
            select(d).where(
                d.message_id == message_id, target == (source_id or recipient_user_id)
            )
        )
        assert row is not None
        return delivery_entity(row)

    async def get(self, delivery_id: UUID) -> Delivery | None:
        row = await self.session.get(ChannelMessageDeliveryModel, delivery_id)
        return delivery_entity(row) if row else None

    async def claim(self, delivery_id: UUID) -> Delivery | None:
        """Take a due delivery to send it, for ``CLAIM_LEASE``. ``None`` if it
        is not due, waits behind an older one to its target, or is a claim
        that lapsed (``interrupted``). By the database's clock, as it was
        planned by, so a sender's clock running behind cannot leave a row
        just planned not yet due."""
        d = ChannelMessageDeliveryModel
        row = await self.session.scalar(
            update(d)
            .where(
                d.id == delivery_id,
                d.status.in_(_DUE),
                d.next_attempt_at <= func.now(),
                d.claimed_at.is_(None),
                ~_behind_an_older_one(),
            )
            .values(
                attempts=d.attempts + 1,
                claimed_at=func.now(),
                next_attempt_at=func.now() + CLAIM_LEASE,
                updated_at=func.now(),
            )
            .returning(d)
        )
        return delivery_entity(row) if row is not None else None

    async def lapsed(self, delivery_id: UUID) -> Delivery | None:
        """The delivery, when it is a claim whose lease ran out unfinished."""
        d = ChannelMessageDeliveryModel
        row = await self.session.scalar(
            select(d).where(
                d.id == delivery_id,
                d.status.in_(_DUE),
                d.claimed_at.is_not(None),
                d.next_attempt_at <= func.now(),
            )
        )
        return delivery_entity(row) if row is not None else None

    async def reclaim(self, delivery_id: UUID) -> Delivery | None:
        """Take a lapsed claim to send it again: only for a send whose
        platform dedupes a resend by the delivery's key, so a send that did
        arrive is not repeated (``agent_surfaces.delivery_outcome``)."""
        d = ChannelMessageDeliveryModel
        row = await self.session.scalar(
            update(d)
            .where(
                d.id == delivery_id,
                d.status.in_(_DUE),
                d.claimed_at.is_not(None),
                d.next_attempt_at <= func.now(),
                d.external_message_id.is_(None),
                d.attempts < MAX_ATTEMPTS,
            )
            .values(
                attempts=d.attempts + 1,
                claimed_at=func.now(),
                next_attempt_at=func.now() + CLAIM_LEASE,
                updated_at=func.now(),
            )
            .returning(d)
        )
        return delivery_entity(row) if row is not None else None

    async def interrupted(self, delivery_id: UUID) -> Delivery | None:
        """Settle a claim that lapsed unfinished, without sending it again:
        ``SENT`` when its platform id was noted before the call, else ``DEAD``."""
        d = ChannelMessageDeliveryModel
        known = d.external_message_id.is_not(None)
        row = await self.session.scalar(
            update(d)
            .where(
                d.id == delivery_id,
                d.status.in_(_DUE),
                d.claimed_at.is_not(None),
                d.next_attempt_at <= func.now(),
            )
            .values(
                status=case(
                    (known, DeliveryStatus.SENT.value),
                    else_=DeliveryStatus.DEAD.value,
                ),
                reason=case((known, None), else_=INTERRUPTED),
                sent_at=case((known, func.now()), else_=None),
                claimed_at=None,
                next_attempt_at=None,
                updated_at=func.now(),
            )
            .returning(d)
        )
        if row is None:
            return None
        await self._shown_again([(row.message_id, row.source_id)])
        return delivery_entity(row)

    async def noted(self, delivery_id: UUID, *, external_message_id: str) -> None:
        """The platform's id for a send, as soon as it is known: before the
        call that finishes a message already there (a claim lapsing after
        this is known to have arrived), or the moment the platform answers (a
        status report about it finds the row even before it is ``SENT``)."""
        d = ChannelMessageDeliveryModel
        await self.session.execute(
            update(d)
            .where(d.id == delivery_id, d.claimed_at.is_not(None))
            .values(external_message_id=external_message_id, updated_at=func.now())
        )

    async def unnoted(self, delivery_id: UUID) -> None:
        """A noted id that no longer stands for anything there (a live
        stream taken down before the answer replaced it)."""
        d = ChannelMessageDeliveryModel
        await self.session.execute(
            update(d)
            .where(d.id == delivery_id, d.claimed_at.is_not(None))
            .values(external_message_id=None, updated_at=func.now())
        )

    async def sent(
        self, delivery_id: UUID, *, platform: str, external_message_id: str | None
    ) -> None:
        d = ChannelMessageDeliveryModel
        changed = await self.session.execute(
            update(d)
            .where(d.id == delivery_id, _settleable())
            .values(
                status=DeliveryStatus.SENT.value,
                platform=platform,
                external_message_id=func.coalesce(
                    external_message_id, d.external_message_id
                ),
                reason=None,
                sent_at=func.now(),
                claimed_at=None,
                next_attempt_at=None,
                updated_at=func.now(),
            )
            .returning(d.message_id, d.source_id)
        )
        await self._shown_again(changed)

    async def skipped(
        self, delivery_id: UUID, *, reason: str, platform: str | None
    ) -> None:
        d = ChannelMessageDeliveryModel
        changed = await self.session.execute(
            update(d)
            .where(d.id == delivery_id, _settleable())
            .values(
                status=DeliveryStatus.SKIPPED.value,
                platform=platform,
                reason=reason,
                claimed_at=None,
                next_attempt_at=None,
                updated_at=func.now(),
            )
            .returning(d.message_id, d.source_id)
        )
        await self._shown_again(changed)

    async def failed(
        self,
        delivery: Delivery,
        *,
        reason: str,
        platform: str | None,
        retry_after: float | None = None,
    ) -> Delivery:
        """It did not go: due again after backoff -- at least as long as the
        platform asked (``retry_after``, seconds) -- or DEAD after the last try."""
        dead = delivery.attempts >= MAX_ATTEMPTS
        wait = backoff(delivery.attempts, retry_after=retry_after)
        d = ChannelMessageDeliveryModel
        row = await self.session.scalar(
            update(d)
            .where(d.id == delivery.id, _settleable())
            .values(
                status=(DeliveryStatus.DEAD if dead else DeliveryStatus.FAILED).value,
                platform=platform,
                reason=reason,
                claimed_at=None,
                next_attempt_at=None if dead else func.now() + wait,
                updated_at=func.now(),
            )
            .returning(d)
        )
        if row is None:
            # A platform's report settled it meanwhile; that stands.
            return await self.get(delivery.id) or delivery
        await self._shown_again([(row.message_id, row.source_id)])
        return delivery_entity(row)

    async def dead(
        self, delivery_id: UUID, *, reason: str, platform: str | None
    ) -> None:
        """Given up now, not after backoff: trying again repeats what the
        person already has (half a long message), or meets the same refusal."""
        d = ChannelMessageDeliveryModel
        changed = await self.session.execute(
            update(d)
            .where(d.id == delivery_id, _settleable())
            .values(
                status=DeliveryStatus.DEAD.value,
                platform=platform,
                reason=reason,
                claimed_at=None,
                next_attempt_at=None,
                updated_at=func.now(),
            )
            .returning(d.message_id, d.source_id)
        )
        await self._shown_again(changed)

    async def retry(self, delivery_id: UUID) -> Delivery | None:
        """A member asked to try a failed or given-up delivery again, now."""
        d = ChannelMessageDeliveryModel
        row = await self.session.scalar(
            update(d)
            .where(
                d.id == delivery_id,
                d.status.in_([DeliveryStatus.FAILED.value, DeliveryStatus.DEAD.value]),
            )
            .values(
                status=DeliveryStatus.PENDING.value,
                attempts=0,
                claimed_at=None,
                next_attempt_at=func.now(),
                updated_at=func.now(),
            )
            .returning(d)
        )
        if row is None:
            return None
        await self._shown_again([(row.message_id, row.source_id)])
        return delivery_entity(row)

    async def undelivered(self, delivery_id: UUID, *, reason: str) -> bool:
        """The platform took a send and reported later that it never arrived:
        given up, since sending it again would meet the same refusal. Only
        the delivery the report names (``by_external``), whether it is
        ``SENT`` yet or still being recorded -- a report can land before the
        sender's own outcome does, and the sender's ``sent`` then leaves it.
        Whether it changed anything."""
        d = ChannelMessageDeliveryModel
        row = await self.session.execute(
            update(d)
            .where(
                d.id == delivery_id,
                or_(
                    d.status == DeliveryStatus.SENT.value,
                    and_(d.status.in_(_DUE), d.claimed_at.is_not(None)),
                ),
            )
            .values(
                status=DeliveryStatus.DEAD.value,
                reason=reason,
                claimed_at=None,
                next_attempt_at=None,
                updated_at=func.now(),
            )
            .returning(d.message_id, d.source_id)
        )
        changed = list(row)
        await self._shown_again(changed)
        return bool(changed)

    async def by_external(
        self, *, platform: str, external_message_id: str, pod_id: UUID | None = None
    ) -> Delivery | None:
        """The delivery a platform's status report names: the newest with that
        id, in ``pod_id`` when the report says whose it is."""
        d = ChannelMessageDeliveryModel
        statement = select(d).where(
            func.md5(d.external_message_id) == func.md5(external_message_id),
            d.external_message_id == external_message_id,
            # A send still being recorded has its id noted and no platform yet.
            or_(d.platform == platform, d.platform.is_(None)),
        )
        if pod_id is not None:
            statement = statement.where(d.pod_id == pod_id)
        row = await self.session.scalar(
            statement.order_by(d.created_at.desc()).limit(1)
        )
        return delivery_entity(row) if row is not None else None

    async def next_for_target(self, delivery: Delivery) -> UUID | None:
        """The oldest delivery to ``delivery``'s target that is due now and no
        longer waits behind another: what to announce once ``delivery`` is
        settled, so the place's next line goes without waiting for a sweep."""
        d = ChannelMessageDeliveryModel
        target = (
            d.source_id == delivery.source_id
            if delivery.source_id is not None
            else d.recipient_user_id == delivery.recipient_user_id
        )
        return await self.session.scalar(
            select(d.id)
            .where(
                target,
                d.id != delivery.id,
                d.status.in_(_DUE),
                d.claimed_at.is_(None),
                d.next_attempt_at <= func.now(),
                ~_behind_an_older_one(),
            )
            .limit(1)
        )

    async def urgent(self, delivery: Delivery) -> bool:
        """Whether a push is one the member must not miss: its line names
        them, or asks a question waiting on them (or on anybody there)."""
        recipient = delivery.recipient_user_id
        if recipient is None:
            return False
        mention, question = ChannelMentionModel, ChannelQuestionModel
        named = exists().where(
            mention.message_id == delivery.message_id, mention.user_id == recipient
        )
        asked = exists().where(
            question.message_id == delivery.message_id,
            or_(
                question.awaits_user_id == recipient,
                and_(
                    question.awaits_user_id.is_(None),
                    question.awaits_contact_id.is_(None),
                ),
            ),
        )
        return bool(await self.session.scalar(select(or_(named, asked))))

    async def held(
        self, *, recipient_user_id: UUID, channel_id: UUID, reason: str
    ) -> list[Delivery]:
        """Pushes to a member, of one channel's lines, skipped with ``reason``
        -- held for a digest -- oldest first, locked for the digest that
        takes them."""
        d = ChannelMessageDeliveryModel
        m = ChannelMessageModel
        rows = await self.session.scalars(
            select(d)
            .join(m, m.id == d.message_id)
            .where(
                d.recipient_user_id == recipient_user_id,
                m.channel_id == channel_id,
                d.status == DeliveryStatus.SKIPPED.value,
                d.reason == reason,
            )
            .order_by(m.seq)
            .limit(MAX_LINES)
            .with_for_update(of=d, skip_locked=True)
        )
        return [delivery_entity(row) for row in rows]

    async def reasoned(self, delivery_ids: Sequence[UUID], *, reason: str) -> None:
        """Say why again, on deliveries that stay as they are (a held push
        that a digest has now told the member of)."""
        if not delivery_ids:
            return
        d = ChannelMessageDeliveryModel
        await self.session.execute(
            update(d)
            .where(d.id.in_(list(delivery_ids)))
            .values(reason=reason, updated_at=func.now())
        )

    async def _shown_again(self, changed: Iterable[tuple[UUID, UUID | None]]) -> None:
        """A send to a place moved: the line's writer sees where it went on
        the line (``ChannelMessageResponse.delivered``), so it is sent to its
        watchers again. A push to a member is not shown on the line."""
        lines = {
            message_id for message_id, source_id in changed if source_id is not None
        }
        if len(lines) == 1:
            await touch_line(self.session, next(iter(lines)))
        else:
            await touch_lines(self.session, lines)

    async def of_messages(self, message_ids: Sequence[UUID]) -> list[Delivery]:
        if not message_ids:
            return []
        rows = await self.session.scalars(
            select(ChannelMessageDeliveryModel)
            .where(
                ChannelMessageDeliveryModel.message_id.in_(
                    list(message_ids)[:MAX_LINES]
                )
            )
            .order_by(ChannelMessageDeliveryModel.created_at)
        )
        return [delivery_entity(row) for row in rows]

    async def due(self, *, limit: int) -> list[UUID]:
        """Deliveries due now, oldest first: what the sweeper hands out. Not
        one waiting behind an older one to its target: that one goes first,
        and announces it (``next_for_target``)."""
        d = ChannelMessageDeliveryModel
        rows = await self.session.scalars(
            select(d.id)
            .where(
                d.status.in_(_DUE),
                d.next_attempt_at <= func.now(),
                ~_behind_an_older_one(),
            )
            .order_by(d.next_attempt_at)
            .limit(max(1, min(limit, MAX_LINES)))
        )
        return list(rows)
