"""What is left of the agent module's stream consumers and crons.

Agent turns, their titles and every follow-up of a finished run are work items
now (``turn_work.py``, ``run_followup_work.py``): the ``agent-events`` group,
``process_agent_run``, ``generate_conversation_title`` and the orphaned-run
sweep that repaired what they lost are gone, and the two Agent Host crons are
periodic work (``periodic_work.py``). What remains here moves with the rest of
the old transport: the memory-brief invalidation (a cache delete on the
datastore's stream).
"""

from __future__ import annotations

from uuid import UUID

from faststream import Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.datastore.contracts import (
    DATASTORE_EVENTS_STREAM,
    DatastoreFileCreatedEvent,
    DatastoreFileDeletedEvent,
    DatastoreFileUpdatedEvent,
)
from app.modules.agent.services.agent_memory_brief import invalidate_memory_brief
from app.core.log.log import get_logger

logger = get_logger(__name__)

router = RedisRouter()


_FILE_WRITE_EVENT_TYPES = frozenset(
    {
        DatastoreFileCreatedEvent.get_event_type(),
        DatastoreFileUpdatedEvent.get_event_type(),
        DatastoreFileDeletedEvent.get_event_type(),
    }
)


@reliable_redis_stream_subscriber(
    router,
    DATASTORE_EVENTS_STREAM,
    group="agent-memory-brief-invalidation",
    consumer="agent-memory-brief-invalidation-consumer",
)
async def on_datastore_file_written(event: dict, fs_logger: Logger):
    """Drop a cached memory section when the file behind it changes.

    The pod tools already invalidate inline, which covers agents writing through
    `pod_write_file`. This covers the other writer, and it is the common one:
    an agent with a shell writes memory with `lemma files write`, which reaches
    the datastore over HTTP in the API process and never enters the worker that
    ran the agent. Without this, the shell path is stale until the TTL.

    No inbox and no idempotency key -- deleting a cache entry twice costs
    nothing, and a delivery this misses costs only the TTL, which is the
    behaviour that existed before any invalidation at all.
    """
    if event.get("event_type") not in _FILE_WRITE_EVENT_TYPES:
        return
    pod_id = event.get("pod_id")
    path = event.get("path")
    if not pod_id or not path:
        return
    actor_id = event.get("actor_id")
    await invalidate_memory_brief(
        pod_id=UUID(str(pod_id)),
        path=str(path),
        user_id=UUID(str(actor_id)) if actor_id else None,
    )
