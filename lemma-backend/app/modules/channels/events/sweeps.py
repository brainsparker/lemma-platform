"""The channels module's recurring work.

* ``channels.questions_expire`` -- every minute, questions whose time ran out
  are closed as expired, and their askers told (``ChannelQuestionClosed``);
  and a question whose answer was claimed by a caller that died is put back to
  wait again (its claim lapsed: ``CLAIM_TIMEOUT``).
* ``channels.deliveries_due`` -- every minute, a delivery that is due again
  (a failure's backoff passed, or a sender that claimed it died) is announced
  once more, so whatever sends it picks it up.
"""

from __future__ import annotations

from datetime import timedelta

from opentelemetry import metrics

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import facts
from app.core.infrastructure.work.periodic_jobs import periodic_job
from app.core.log.log import get_logger
from app.modules.channels.contracts.deliveries import due_fact
from app.modules.channels.infrastructure.deliveries import DeliveryRepository
from app.modules.channels.services.questions import expire_overdue, release_lapsed

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
questions_released = meter.create_counter(
    "lemma.channels.questions_released",
    description=(
        "Questions the sweeper opened again: the caller answering one died "
        "holding its claim."
    ),
)
questions_expired = meter.create_counter(
    "lemma.channels.questions_expired",
    description="Questions the sweeper closed because their time ran out.",
)
deliveries_due_again = meter.create_counter(
    "lemma.channels.deliveries_due_again",
    description=(
        "Deliveries the sweeper announced again: a failure's backoff passed, "
        "or the sender that claimed one died."
    ),
)

QUESTIONS_EXPIRE = "channels.questions_expire"
DELIVERIES_DUE = "channels.deliveries_due"

#: One sweep's batch; a backlog is worked off over the following minutes.
_BATCH = 200


@periodic_job(QUESTIONS_EXPIRE, every=timedelta(minutes=1))
async def questions_expire() -> None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        # Expiry first: a lapsed claim past its time closes, not reopens.
        expired = await expire_overdue(uow, limit=_BATCH)
        released = await release_lapsed(uow, limit=_BATCH)
        await uow.commit()
    if released:
        questions_released.add(released)
        logger.warning(
            "channels.questions.claim_lapsed.degraded",
            count=released,
            backlog=released >= _BATCH,
        )
    if expired:
        questions_expired.add(expired)
        logger.info(
            "channels.questions.expired.observed",
            count=expired,
            backlog=expired >= _BATCH,
        )


@periodic_job(DELIVERIES_DUE, every=timedelta(minutes=1))
async def deliveries_due() -> None:
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        repository = DeliveryRepository(uow.session)
        due = await repository.due(limit=_BATCH)
        for delivery_id in due:
            delivery = await repository.get(delivery_id)
            if delivery is not None:
                await facts.emit(uow, due_fact(delivery))
        await uow.commit()
    if due:
        deliveries_due_again.add(len(due))
        logger.info(
            "channels.deliveries.due_again.observed",
            count=len(due),
            backlog=len(due) >= _BATCH,
        )
