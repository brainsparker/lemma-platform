"""What is left of the agent module's stream consumers and crons.

Agent turns, their titles and every follow-up of a finished run are work items
now (``turn_work.py``, ``run_followup_work.py``): the ``agent-events`` group,
``process_agent_run``, ``generate_conversation_title`` and the orphaned-run
sweep that repaired what they lost are gone. What remains here moves with the
rest of the old transport: the memory-brief invalidation (a cache delete on
the datastore's stream) and the two Agent Host crons.
"""

from __future__ import annotations

from uuid import UUID

from faststream import Logger
from faststream.redis import RedisRouter
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.infrastructure.jobs.streaq_runtime import (
    AppWorkerContext,
    streaq_cron,
    streaq_worker,
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


@streaq_cron("1-59/5 * * * *", name="reconcile_agent_host_dispatch")
async def reconcile_agent_host_dispatch() -> None:
    """Reconcile Agent Host leases against the runs they belong to.

    Two things nobody else does once the worker driving a run is gone. Cancel
    host runs whose Lemma run already ended, so a machine on someone's desk
    stops executing tools for a turn we have reported as failed. And advance
    leases whose heartbeat lapsed, which otherwise stay non-terminal forever and
    are never collected by retention.
    """
    from app.modules.agent.infrastructure.agent_host import recovery
    from app.modules.agent.infrastructure.agent_host.channels import poke_host

    worker_ctx: AppWorkerContext = streaq_worker.context
    # Only database trouble is swallowed here: it is the transient failure this
    # sweep expects, and the next tick is five minutes away. Anything else is a
    # bug and surfaces through the worker's own job-failure path.
    # Two transactions, not one, and that is a deadlock fix rather than a
    # style choice. Both halves touch leases and commands; run together they
    # hold lease locks from `cancel_abandoned_host_runs` while
    # `reconcile_expired_leases` goes on to take command locks. A host's
    # `control` frame walks the same two tables the other way round -- commands
    # first, then a blocking lease lock -- so the two can wedge (ABBA), and
    # Postgres aborts one with 40P01. The link retries that once
    # (`AgentHostLinkStore.apply_control`); this removes the cause.
    #
    # Committing between them means no lease lock is ever held across a command
    # acquisition, which removes this side of the cycle. The two sweeps are
    # independent -- one cancels runs Lemma already finished, the other advances
    # lapsed leases -- so splitting them costs nothing but a second round trip
    # every five minutes.
    try:
        async with worker_ctx.uow() as uow:
            host_ids = await recovery.cancel_abandoned_host_runs(uow.session)
            await uow.commit()
        async with worker_ctx.uow() as uow:
            await recovery.reconcile_expired_leases(uow.session)
            await uow.commit()
    except SQLAlchemyError:
        logger.error(
            "agent.handlers.reconcile_agent_host_dispatch_cron.failed", exc_info=True
        )
        return
    # Poke outside the transaction: without it the cancel waits for the link's
    # 5-second floor instead of going out now. poke_host never raises.
    for host_id in dict.fromkeys(host_ids):
        await poke_host(host_id)


@streaq_cron("23 4 * * *", name="cleanup_agent_host_retained_state")
async def cleanup_agent_host_retained_state() -> None:
    """Collect spent Agent Host pairings, commands, and leases.

    Without this registration the sweep existed but never ran, so dispatch rows
    accumulated for the lifetime of the deployment and consumed pairing codes
    were never purged.
    """
    from app.modules.agent.infrastructure.agent_host import recovery

    worker_ctx: AppWorkerContext = streaq_worker.context
    try:
        async with worker_ctx.uow() as uow:
            await recovery.cleanup_retained_state(uow.session)
            await uow.commit()
    except SQLAlchemyError:
        logger.error(
            "agent.handlers.cleanup_agent_host_retained_state_cron.failed",
            exc_info=True,
        )
