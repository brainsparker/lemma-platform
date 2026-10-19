"""Deliveries: each send of a line outside Lemma, and the only record of it.

A delivery row is made once per line and target (a place, or a member pushed
to) before anything is sent. A worker claims a due row, sends, and records
the outcome on the same row -- so a retried job finds ``SENT`` and stops,
and a failure is retried with backoff until it is ``DEAD``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import Delivery, DeliveryStatus
from app.modules.channels.infrastructure.models import ChannelMessageDeliveryModel
from app.modules.channels.infrastructure.rows import MAX_LINES, delivery_entity, now

#: Tries before a delivery is given up on.
MAX_ATTEMPTS = 6


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
        """Take a due delivery to send it: it is due again in a minute if the
        sender dies, and then another may take it. ``None`` if not due."""
        d = ChannelMessageDeliveryModel
        row = await self.session.scalar(
            update(d)
            .where(
                d.id == delivery_id,
                d.status.in_(
                    [DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value]
                ),
                d.next_attempt_at <= at,
            )
            .values(
                attempts=d.attempts + 1,
                next_attempt_at=at + timedelta(minutes=1),
                updated_at=at,
            )
            .returning(d)
        )
        return delivery_entity(row) if row is not None else None

    async def sent(
        self, delivery_id: UUID, *, platform: str, external_message_id: str | None
    ) -> None:
        d = ChannelMessageDeliveryModel
        await self.session.execute(
            update(d)
            .where(d.id == delivery_id)
            .values(
                status=DeliveryStatus.SENT.value,
                platform=platform,
                external_message_id=external_message_id,
                reason=None,
                sent_at=now(),
                next_attempt_at=None,
                updated_at=now(),
            )
        )

    async def skipped(
        self, delivery_id: UUID, *, reason: str, platform: str | None
    ) -> None:
        d = ChannelMessageDeliveryModel
        await self.session.execute(
            update(d)
            .where(d.id == delivery_id)
            .values(
                status=DeliveryStatus.SKIPPED.value,
                platform=platform,
                reason=reason,
                next_attempt_at=None,
                updated_at=now(),
            )
        )

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
                next_attempt_at=None if dead else now() + backoff(delivery.attempts),
                updated_at=now(),
            )
            .returning(d)
        )
        assert row is not None
        return delivery_entity(row)

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
                next_attempt_at=now(),
                updated_at=now(),
            )
            .returning(d)
        )
        return delivery_entity(row) if row is not None else None

    async def undelivered(self, *, message_id: UUID, platform: str, reason: str) -> int:
        """The platform took a line and reported later that it never arrived:
        given up, since sending it again would meet the same refusal."""
        d = ChannelMessageDeliveryModel
        rows = await self.session.scalars(
            update(d)
            .where(
                d.message_id == message_id,
                d.platform == platform,
                d.status == DeliveryStatus.SENT.value,
            )
            .values(
                status=DeliveryStatus.DEAD.value,
                reason=reason,
                next_attempt_at=None,
                updated_at=now(),
            )
            .returning(d.id)
        )
        return len(list(rows))

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
            )
            .limit(1)
        )
        return delivery_entity(row) if row is not None else None

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
                d.status.in_(
                    [DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value]
                ),
                d.next_attempt_at <= at,
            )
            .order_by(d.next_attempt_at)
            .limit(max(1, min(limit, MAX_LINES)))
        )
        return list(rows)
