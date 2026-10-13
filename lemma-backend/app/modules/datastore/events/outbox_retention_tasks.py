"""Retention for the datastore database's own event outbox.

When pod schemas live in a database of their own, every record write stages
its event in *that* database's ``domain_event_outbox`` (see
``transactional_events.ensure_datastore_event_outbox``), and the datastore
outbox dispatcher publishes it from there. The hourly
``prune_event_delivery_records`` cron only ever opened the main database, so
this second outbox was never pruned: on a development install it reached 5.7M
published rows and 13 GB, filled the disk, and slowed the dispatcher's claim
query past its statement timeout.

Same rules and the same settings as the main outbox -- published rows past
``EVENT_COMPLETED_RETENTION_DAYS``, dead letters past
``EVENT_DEAD_LETTER_RETENTION_DAYS`` -- and nothing else: this database has no
inbox. Offset from the main sweep's minute so the two never contend for the
same worker slot.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.infrastructure.events.retention import prune_event_delivery_records
from app.core.infrastructure.jobs.streaq_runtime import Lane, streaq_cron
from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings

logger = get_logger(__name__)


def datastore_outbox_is_separate() -> bool:
    """True when record events are staged outside the main database.

    When the two URLs match there is one outbox table, and the main sweep
    already covers it; pruning it twice would only race the same rows.
    """
    datastore_url = datastore_settings.datastore_database_url or settings.database_url
    return datastore_url != settings.database_url


async def prune_datastore_outbox() -> dict[str, int] | None:
    """One sweep; None when there is no separate datastore outbox to prune."""
    if not datastore_outbox_is_separate():
        return None
    from app.modules.datastore.infrastructure.session import (
        get_datastore_session_maker,
    )

    return await prune_event_delivery_records(
        get_datastore_session_maker(), include_inbox=False
    )


@streaq_cron("37 * * * *", name="prune_datastore_outbox", lane=Lane.BULK)
async def prune_datastore_outbox_task() -> None:
    deleted = await prune_datastore_outbox()
    if deleted and (total := sum(deleted.values())):
        logger.debug(
            "datastore.outbox_retention.pruned.observed",
            deleted_count=total,
        )
