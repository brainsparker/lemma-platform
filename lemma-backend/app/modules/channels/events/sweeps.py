"""The channels module's recurring work.

* ``channels.questions_expire`` -- every minute, questions whose time ran out
  are closed as expired, and their askers told (``ChannelQuestionClosed``).
* ``channels.deliveries_due`` -- every minute, a delivery that is due again
  (a failure's backoff passed, or a sender that claimed it died) is announced
  once more, so whatever sends it picks it up.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import facts
from app.core.infrastructure.work.periodic_jobs import periodic_job
from app.core.log.log import get_logger
from app.modules.channels.contracts.facts import ChannelDeliveryDue
from app.modules.channels.infrastructure.deliveries import DeliveryRepository
from app.modules.channels.infrastructure.rows import now
from app.modules.channels.services.questions import expire_overdue

logger = get_logger(__name__)

QUESTIONS_EXPIRE = "channels.questions_expire"
DELIVERIES_DUE = "channels.deliveries_due"

#: One sweep's batch; a backlog is worked off over the following minutes.
_BATCH = 200


@periodic_job(QUESTIONS_EXPIRE, every=timedelta(minutes=1))
async def questions_expire() -> None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        expired = await expire_overdue(uow, limit=_BATCH)
        await uow.commit()
    if expired:
        logger.info("channels.questions.expired.observed", count=expired)


@periodic_job(DELIVERIES_DUE, every=timedelta(minutes=1))
async def deliveries_due() -> None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        repository = DeliveryRepository(uow.session)
        due = await repository.due(at=now(), limit=_BATCH)
        for delivery_id in due:
            delivery = await repository.get(delivery_id)
            if delivery is not None:
                await facts.emit(
                    uow,
                    ChannelDeliveryDue(pod_id=delivery.pod_id, delivery_id=delivery_id),
                )
        await uow.commit()
    if due:
        logger.info("channels.deliveries.due_again.observed", count=len(due))
