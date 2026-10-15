"""``surface.reply``: the destination pump that sends ``surface_deliveries`` rows.

One item per destination (a Slack channel, a Telegram chat, a WhatsApp sender),
concurrency 1 on that key. Each run sends the destination's rows oldest first
until none is due:

* a row still SENDING past its deadline was being sent by somebody who died --
  this pump on an earlier attempt, or a run's observer -- and nobody can say
  whether it arrived. It is marked UNKNOWN and never sent again;
* a row still SENDING within its deadline is somebody's send in flight (the
  observer closing a stream with the answer): the pump waits for it;
* a row not yet due (a ``Retry-After``, the observer's grace) holds everything
  behind it, so nothing overtakes it;
* otherwise the row is claimed SENDING in a committed checkpoint *before* the
  platform call, and settled after it.

A transient refusal reschedules the row behind its ``Retry-After`` and defers
the pump to the same moment, charging nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import (
    Defer,
    JobContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.deliveries import (
    SENDING_LEASE,
    DeliveryStatus,
)
from app.modules.agent_surfaces.events.work_kinds import SURFACE_REPLY
from app.modules.agent_surfaces.infrastructure.repositories.delivery_repository import (
    DeliveryRepository,
)
from app.modules.agent_surfaces.services.delivery_sender import (
    DeliverySender,
    PlatformDeliverySender,
)
from app.modules.agent_surfaces.services.surface_deliveries import (
    PumpPayload,
    settle_claimed_send,
)

logger = get_logger(__name__)

#: Rows one pump run sends before it yields its slot to other destinations.
_ROWS_PER_RUN = 50


def _default_sender() -> DeliverySender:
    from app.modules.agent_surfaces.composition import build_surface_egress

    return PlatformDeliverySender(
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        egress_factory=build_surface_egress,
    )


def make_pump(
    *, sender: DeliverySender | None = None, sending_lease: timedelta = SENDING_LEASE
):
    async def pump(ctx: JobContext, payload: PumpPayload) -> None:
        send = sender or _default_sender()
        for _ in range(_ROWS_PER_RUN):
            ctx.raise_if_stopping()
            async with ctx.checkpoint() as uow:
                repository = DeliveryRepository(uow.session)
                row = await repository.oldest_open(payload.destination_key)
                if row is None:
                    return
                now = datetime.now(UTC)
                if row.status == DeliveryStatus.SENDING.value:
                    if row.sending_until is not None and row.sending_until > now:
                        raise Defer(row.sending_until - now)
                    await repository.set_delivery_status(
                        row.id,
                        DeliveryStatus.UNKNOWN,
                        error="the send was interrupted; it may or may not have arrived",
                    )
                    logger.warning(
                        "agent_surfaces.reply_work.send_outcome_unknown.degraded",
                        delivery_id=str(row.id),
                        destination=payload.destination_key,
                    )
                    continue
                if row.not_before > now:
                    raise Defer(row.not_before - now)
                if not await repository.claim(row.id, lease=sending_lease):
                    continue
                await uow.session.refresh(row)
            result = await send(row)
            async with ctx.checkpoint() as uow:
                delay = await settle_claimed_send(uow, row.id, result)
            if delay is not None:
                raise Defer(delay)
        # More than one run's worth: go round again, behind other destinations.
        raise Defer(timedelta(0))

    return pump


def register_surface_reply_work(
    registry: WorkRegistry,
    *,
    sender: DeliverySender | None = None,
    sending_lease: timedelta = SENDING_LEASE,
) -> None:
    registry.handler(
        kind=SURFACE_REPLY,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=PumpPayload,
        concurrency=1,
        # A failure here is the database, not the platform: every platform
        # refusal is recorded on the row and deferred, never raised.
        retry=RetryPolicy(max_attempts=5, max_lease_losses=10),
    )(make_pump(sender=sender, sending_lease=sending_lease))


register_surface_reply_work(default_registry)
