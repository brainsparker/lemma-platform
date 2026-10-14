"""Periodic work owned by the durable event transport, until it is removed.

Both go with the outbox and the consumer groups (PR 3b): until then the outbox
still fills and groups can still be abandoned.
"""

from datetime import timedelta

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.events.config import event_transport_settings
from app.core.infrastructure.events.retention import prune_event_delivery_records
from app.core.infrastructure.work import periodic_job
from app.core.log.log import get_logger

logger = get_logger(__name__)


@periodic_job("events.prune_delivery_records", every=timedelta(hours=1))
async def prune_event_delivery_records_task() -> None:
    deleted = await prune_event_delivery_records(async_session_maker)
    if total := sum(deleted.values()):
        logger.debug(
            "infrastructure.tasks.pruned_durable_event_delivery_records.observed",
            deleted_count=total,
        )


@periodic_job("events.reap_abandoned_consumer_groups", every=timedelta(hours=1))
async def reap_abandoned_consumer_groups_task() -> None:
    """Hourly report (and, when enabled, removal) of groups nothing reads.

    This cron used to trim streams to their byte budget as well. That moved to
    the worker's stream guard, which runs every few seconds rather than every
    hour -- an hour of a bulk import is more than a small Redis holds -- and
    which can trim past a group like these instead of being pinned by it.
    """
    from app.core.infrastructure.events.group_reaper import (
        reap_abandoned_consumer_groups,
    )
    from app.core.infrastructure.redis.client import get_redis

    abandoned = await reap_abandoned_consumer_groups(get_redis())
    if abandoned:
        # "detected", not "reaped": destruction is off by default, so the usual
        # reading of this line is "these are candidates", and a line that says
        # they were reaped when nothing was would be read once and trusted
        # afterwards. `destroyed` carries which it was.
        logger.info(
            "redis.stream.abandoned_consumer_groups_detected.observed",
            group_count=len(abandoned),
            destroyed=event_transport_settings.redis_stream_group_destroy_enabled,
        )
