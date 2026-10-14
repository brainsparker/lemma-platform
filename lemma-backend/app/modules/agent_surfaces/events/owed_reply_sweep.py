"""Every ten minutes, re-queue the owed replies whose attempt was lost.

The work is `services/owed_reply_sweep`; this is only its schedule.
"""

from __future__ import annotations

from app.core.infrastructure.db.session import get_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_runtime import Lane, streaq_cron
from app.core.log.log import get_logger
from app.modules.agent_surfaces.services.owed_reply_sweep import sweep_owed_replies

logger = get_logger(__name__)


@streaq_cron("3-59/10 * * * *", name="sweep_owed_replies", lane=Lane.BULK)
async def sweep_owed_replies_task() -> None:
    swept = await sweep_owed_replies(SessionUnitOfWorkFactory(get_session_maker()))
    logger.info(
        "agent_surfaces.tasks.sweep_owed_replies.observed",
        requeued=swept.requeued,
        expired=swept.expired,
    )
