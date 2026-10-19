"""``surface.channel_delivery``: one channel delivery, sent.

Subscribed to ``ChannelDeliveryDue``, which channels announces when it plans a
delivery and again when one is due after a failure. A JOB: it calls a
platform. The delivery row is the record -- a retry claims it again only if
it is still due, so one that was sent is never sent twice.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_CHANNEL_DELIVERY,
    channel_delivery_key,
)
from app.modules.channels.contracts.facts import ChannelDeliveryDue


class ChannelDeliveryPayload(BaseModel):
    """The fields of ``ChannelDeliveryDue`` this needs; the rest are ignored."""

    delivery_id: UUID


def make_channel_delivery(uow_factory: UnitOfWorkFactory | None = None):
    async def channel_delivery(
        ctx: JobContext, payload: ChannelDeliveryPayload
    ) -> None:
        del ctx
        from app.modules.agent_surfaces.composition import build_channel_deliveries

        await build_channel_deliveries(
            uow_factory or SessionUnitOfWorkFactory(async_session_maker)
        ).deliver(payload.delivery_id)

    return channel_delivery


def register_channel_delivery_work(
    registry: WorkRegistry, *, uow_factory: UnitOfWorkFactory | None = None
) -> None:
    registry.handler(
        kind=SURFACE_CHANNEL_DELIVERY,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=ChannelDeliveryPayload,
    )(make_channel_delivery(uow_factory))


register_channel_delivery_work(default_registry)
facts.subscribe(
    ChannelDeliveryDue,
    kind=SURFACE_CHANNEL_DELIVERY,
    cls=WorkClass.DELIVERY,
    dedup=lambda fact: channel_delivery_key(fact.delivery_id),
)
