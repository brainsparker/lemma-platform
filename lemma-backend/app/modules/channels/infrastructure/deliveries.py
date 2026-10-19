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
answer closes) it is ``SENT``, since the message is there; otherwise it is
``DEAD`` with a reason saying it may or may not have arrived, and a member can
retry it. Saying something twice is worse than saying, visibly, that it may
not have arrived.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import Delivery, DeliveryStatus
from app.modules.channels.infrastructure.models import ChannelMessageDeliveryModel
from app.modules.channels.infrastructure.revisions import touch_line, touch_lines
from app.modules.channels.infrastructure.rows import MAX_LINES, delivery_entity, now

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


def backoff(attempts: int) -> timedelta:
    """How long after the ``attempts``-th failure to try again: 30 s, 2 min,
    8 min, 32 min, ~2 h."""
    return timedelta(seconds=30 * 4 ** max(0, attempts - 1))


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

    async def claim(self, delivery_id: UUID, *, at: datetime) -> Delivery | None:
        """Take a due delivery to send it, for ``CLAIM_LEASE``. ``None`` if it
        is not due -- or is a claim that lapsed (``interrupted``)."""
        d = ChannelMessageDeliveryModel
        row = await self.session.scalar(
            update(d)
            .where(
                d.id == delivery_id,
                d.status.in_(_DUE),
                d.next_attempt_at <= at,
                d.claimed_at.is_(None),
            )
            .values(
                attempts=d.attempts + 1,
                claimed_at=at,
                next_attempt_at=at + CLAIM_LEASE,
                updated_at=at,
            )
            .returning(d)
        )
        return delivery_entity(row) if row is not None else None

    async def interrupted(self, delivery_id: UUID, *, at: datetime) -> Delivery | None:
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
                d.next_attempt_at <= at,
            )
            .values(
                status=case(
                    (known, DeliveryStatus.SENT.value),
                    else_=DeliveryStatus.DEAD.value,
                ),
                reason=case((known, None), else_=INTERRUPTED),
                sent_at=case((known, at), else_=None),
                claimed_at=None,
                next_attempt_at=None,
                updated_at=at,
            )
            .returning(d)
        )
        if row is None:
            return None
        await self._shown_again([(row.message_id, row.source_id)])
        return delivery_entity(row)

    async def noted(self, delivery_id: UUID, *, external_message_id: str) -> None:
        """The platform's id for a send about to finish a message already
        there: a claim lapsing after this is known to have arrived."""
        d = ChannelMessageDeliveryModel
        await self.session.execute(
            update(d)
            .where(d.id == delivery_id, d.claimed_at.is_not(None))
            .values(external_message_id=external_message_id, updated_at=now())
        )

    async def sent(
        self, delivery_id: UUID, *, platform: str, external_message_id: str | None
    ) -> None:
        d = ChannelMessageDeliveryModel
        changed = await self.session.execute(
            update(d)
            .where(d.id == delivery_id)
            .values(
                status=DeliveryStatus.SENT.value,
                platform=platform,
                external_message_id=func.coalesce(
                    external_message_id, d.external_message_id
                ),
                reason=None,
                sent_at=now(),
                claimed_at=None,
                next_attempt_at=None,
                updated_at=now(),
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
            .where(d.id == delivery_id)
            .values(
                status=DeliveryStatus.SKIPPED.value,
                platform=platform,
                reason=reason,
                claimed_at=None,
                next_attempt_at=None,
                updated_at=now(),
            )
            .returning(d.message_id, d.source_id)
        )
        await self._shown_again(changed)

    async def failed(
        self, delivery: Delivery, *, reason: str, platform: str | None
    ) -> Delivery:
        """It did not go: due again after backoff, or DEAD after the last try."""
        dead = delivery.attempts >= MAX_ATTEMPTS
        d = ChannelMessageDeliveryModel
        row = await self.session.scalar(
            update(d)
            .where(d.id == delivery.id)
            .values(
                status=(DeliveryStatus.DEAD if dead else DeliveryStatus.FAILED).value,
                platform=platform,
                reason=reason,
                claimed_at=None,
                next_attempt_at=None if dead else now() + backoff(delivery.attempts),
                updated_at=now(),
            )
            .returning(d)
        )
        assert row is not None
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
            .where(d.id == delivery_id)
            .values(
                status=DeliveryStatus.DEAD.value,
                platform=platform,
                reason=reason,
                claimed_at=None,
                next_attempt_at=None,
                updated_at=now(),
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
                next_attempt_at=now(),
                updated_at=now(),
            )
            .returning(d)
        )
        if row is None:
            return None
        await self._shown_again([(row.message_id, row.source_id)])
        return delivery_entity(row)

    async def undelivered(
        self, *, platform: str, external_message_id: str, reason: str
    ) -> int:
        """The platform took a send and reported later, by its id, that it
        never arrived: given up, since sending it again would meet the same
        refusal. How many deliveries that was (none for an id not ours)."""
        d = ChannelMessageDeliveryModel
        rows = await self.session.execute(
            update(d)
            .where(
                d.platform == platform,
                func.md5(d.external_message_id) == func.md5(external_message_id),
                d.external_message_id == external_message_id,
                d.status == DeliveryStatus.SENT.value,
            )
            .values(
                status=DeliveryStatus.DEAD.value,
                reason=reason,
                next_attempt_at=None,
                updated_at=now(),
            )
            .returning(d.message_id, d.source_id)
        )
        changed = list(rows)
        await self._shown_again(changed)
        return len(changed)

    async def by_external(
        self, *, platform: str, external_message_id: str
    ) -> Delivery | None:
        """The delivery a platform's status report names."""
        d = ChannelMessageDeliveryModel
        row = await self.session.scalar(
            select(d)
            .where(
                d.platform == platform,
                func.md5(d.external_message_id) == func.md5(external_message_id),
                d.external_message_id == external_message_id,
            )
            .limit(1)
        )
        return delivery_entity(row) if row is not None else None

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

    async def due(self, *, at: datetime, limit: int) -> list[UUID]:
        """Deliveries due now, oldest first: what the sweeper hands out."""
        d = ChannelMessageDeliveryModel
        rows = await self.session.scalars(
            select(d.id)
            .where(
                d.status.in_(_DUE),
                d.next_attempt_at <= at,
            )
            .order_by(d.next_attempt_at)
            .limit(max(1, min(limit, MAX_LINES)))
        )
        return list(rows)
