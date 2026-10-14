"""Worker event handlers for agent runs."""

from __future__ import annotations

from uuid import UUID

from faststream import Depends, Logger
from faststream.redis import RedisRouter
from sqlalchemy.exc import SQLAlchemyError
from streaq.task import TaskStatus

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.infrastructure.jobs.job_liveness import dead_job_ids
from app.core.infrastructure.jobs.streaq_job_queue import (
    SharedStreaqJobQueue,
    get_streaq_job_queue,
)
from app.core.infrastructure.jobs.streaq_runtime import (
    AGENT_RUN_JOB_TIMEOUT_SECONDS,
    AppWorkerContext,
    streaq_cron,
    streaq_task,
    streaq_worker,
)
from app.modules.datastore.contracts import (
    DATASTORE_EVENTS_STREAM,
    DatastoreFileCreatedEvent,
    DatastoreFileDeletedEvent,
    DatastoreFileUpdatedEvent,
)
from app.modules.agent.services.agent_memory_brief import invalidate_memory_brief
from app.modules.agent.domain.events import (
    AGENT_EVENTS_STREAM,
    AgentRunCompletedEvent,
    AgentRunStartedEvent,
    AgentRunStopRequestedEvent,
)
from app.modules.agent.domain.value_objects import AgentRunStatus
from app.modules.agent.infrastructure.repositories.stuck_stops import (
    settle_stuck_stops,
)
from app.modules.agent.infrastructure.harnesses import (
    HarnessRegistry,
    PydanticAIHarness,
    RemoteHarness,
)
from app.modules.agent.infrastructure.harnesses.agent_host.artifacts import (
    PodFileAgentHostArtifactWriter,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository

# Imported for its side effect too: registering the approval job on the worker.
from app.modules.agent.events.approval_jobs import (  # noqa: F401
    reconcile_agent_approval,
    reconcile_agent_approval_now,
)
from app.modules.agent.events.finished_run import settle_finished_run
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.realtime import (
    completed_payload,
    publish_conversation_event,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

router = RedisRouter()

CONTROL_EVENT_MODELS = {
    AgentRunStartedEvent.get_event_type(): AgentRunStartedEvent,
    AgentRunStopRequestedEvent.get_event_type(): AgentRunStopRequestedEvent,
    AgentRunCompletedEvent.get_event_type(): AgentRunCompletedEvent,
}


_FILE_WRITE_EVENT_TYPES = frozenset(
    {
        DatastoreFileCreatedEvent.get_event_type(),
        DatastoreFileUpdatedEvent.get_event_type(),
        DatastoreFileDeletedEvent.get_event_type(),
    }
)


def agent_run_job_id(agent_run_id: UUID) -> str:
    return f"agent-run:{agent_run_id}"


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


def conversation_title_job_id(conversation_id: UUID) -> str:
    return f"conv-title:{conversation_id}"


def provide_job_queue() -> SharedStreaqJobQueue:
    return get_streaq_job_queue()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def build_harness_registry() -> HarnessRegistry:
    uow_factory = provide_uow_factory()
    return HarnessRegistry(
        [
            PydanticAIHarness(),
            # One harness for every Agent Host tool; which one runs is decided
            # by the profile's harness_id, not by the registry.
            RemoteHarness(
                uow_factory,
                # Without a writer the harness drops every image an agent
                # produces: a content block with no text renders to nothing,
                # so Codex's `$imagegen` output reached this process and was
                # discarded. The host already publishes those blocks and the
                # writer already knows how to store them.
                artifact_writer=PodFileAgentHostArtifactWriter(uow_factory),
            ),
        ]
    )


@reliable_redis_stream_subscriber(
    router,
    AGENT_EVENTS_STREAM,
    group="agent-events",
    consumer="agent-events-consumer",
)
async def handle_agent_control_event(
    event: dict,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue = Depends(provide_job_queue),
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
):
    event_model = CONTROL_EVENT_MODELS.get(event.get("event_type"))
    if event_model is None:
        return

    async def process() -> None:
        parsed = event_model.model_validate(event)
        await _process_agent_control_event(
            parsed,
            fs_logger=fs_logger,
            job_queue=job_queue,
            uow_factory=uow_factory,
        )

    await inbox.process("agent.control", event, process)


async def _process_agent_control_event(
    parsed: AgentRunStartedEvent | AgentRunStopRequestedEvent | AgentRunCompletedEvent,
    *,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue,
    uow_factory: UnitOfWorkFactory,
) -> None:
    if isinstance(parsed, AgentRunStartedEvent):
        await enqueue_agent_run(parsed, fs_logger=fs_logger, job_queue=job_queue)
        # The title only needs the user's first message -- already saved by
        # the time this event fires -- not the agent's reply, so it does not
        # need to wait for the run to finish. Starting it here rather than on
        # completion means a slow or long-running turn no longer leaves the
        # conversation title-less for its whole duration. The deterministic
        # job id dedups across turns, so this runs at most once per
        # conversation; the job itself no-ops if a title already exists.
        await job_queue.enqueue(
            "generate_conversation_title",
            context={"conversation_id": str(parsed.conversation_id)},
            _job_id=conversation_title_job_id(parsed.conversation_id),
        )
        return
    if isinstance(parsed, AgentRunCompletedEvent):
        # Belt and suspenders: the same deterministic job id means this is a
        # no-op on the (now-common) path where the run-started enqueue above
        # already generated the title, and a fallback for anything that
        # reaches completion without having gone through that event.
        await job_queue.enqueue(
            "generate_conversation_title",
            context={"conversation_id": str(parsed.conversation_id)},
            _job_id=conversation_title_job_id(parsed.conversation_id),
        )
        # Queued messages, a parent's wait, an answer another pod is owed.
        await settle_finished_run(parsed, uow_factory=uow_factory)
        return
    if isinstance(parsed, AgentRunStopRequestedEvent):
        job_id = agent_run_job_id(parsed.agent_run_id)
        task_status = await job_queue.status(job_id)
        if task_status == TaskStatus.RUNNING:
            return

        # Do not call streaq abort here. A queued task can race into RUNNING
        # between status() and abort(), and aborting that internal cancel scope
        # can poison the worker task. Mark the run terminal instead; if the
        # streaq task later starts, process_agent_run exits as a no-op.
        event_data = {
            "aborted": False,
            "task_status": task_status.value,
        }
        async with uow_factory() as uow:
            repo = ConversationRepository(uow)
            finish_result = await repo.finish_agent_run(
                agent_run_id=parsed.agent_run_id,
                status=AgentRunStatus.STOPPED,
            )
            if finish_result is not None and finish_result.updated:
                repo.collect_events(
                    [
                        AgentRunCompletedEvent(
                            conversation_id=parsed.conversation_id,
                            agent_run_id=parsed.agent_run_id,
                            status=finish_result.status,
                            data=event_data,
                        )
                    ]
                )
        if finish_result is None or not finish_result.updated:
            return

        await publish_conversation_event(
            parsed.conversation_id,
            completed_payload(
                conversation_id=parsed.conversation_id,
                agent_run_id=parsed.agent_run_id,
                status=finish_result.status.value,
                data=event_data,
            ),
        )


async def enqueue_agent_run(
    event: AgentRunStartedEvent,
    *,
    fs_logger: Logger,
    job_queue: SharedStreaqJobQueue,
) -> bool:
    job = await job_queue.enqueue(
        "process_agent_run",
        context={
            "agent_run_id": str(event.agent_run_id),
            "conversation_id": str(event.conversation_id),
            "user_id": str(event.user_id),
            "pod_id": str(event.pod_id),
            "agent_name": event.agent_name,
        },
        _job_id=agent_run_job_id(event.agent_run_id),
    )
    if job is None:
        return False
    return True


@streaq_task(name="process_agent_run", timeout=AGENT_RUN_JOB_TIMEOUT_SECONDS)
async def process_agent_run(
    context: dict[str, str | None],
):
    worker_ctx: AppWorkerContext = streaq_worker.context
    agent_run_id = UUID(str(context["agent_run_id"]))
    user_id = UUID(str(context["user_id"]))
    pod_id = UUID(str(context["pod_id"]))
    agent_name = context.get("agent_name")

    runner = AgentRunnerService(
        uow_factory=worker_ctx.uow_factory,
        harness_registry=build_harness_registry(),
    )
    from app.modules.agent_surfaces.contracts.egress import build_progress_observer

    # No try/except around this. A CancelledError from a worker shutting down
    # must reach streaq: it only XACKs a task that returned, and relinquishes a
    # cancelled one for the next worker to reclaim. See
    # `AgentRunnerService.execute`.
    await runner.execute(
        agent_run_id=agent_run_id,
        user_id=user_id,
        pod_id=pod_id,
        agent_name=agent_name,
        observer=build_progress_observer(uow_factory=worker_ctx.uow_factory),
    )


@streaq_task(name="generate_conversation_title")
async def process_conversation_title(
    context: dict[str, str | None],
):
    from app.modules.agent.services.conversation_title_service import (
        ConversationTitleService,
    )

    worker_ctx: AppWorkerContext = streaq_worker.context
    conversation_id = UUID(str(context["conversation_id"]))
    await ConversationTitleService(
        uow_factory=worker_ctx.uow_factory
    ).generate_title_if_absent(conversation_id)


# Two cutoffs, because there are two ways to know a run is over. A job that has
# stopped renewing its heartbeat is gone now, however it died, so the first has
# only to outlast one expired heartbeat. A run whose job never reported one --
# still queued, or in flight when the heartbeat shipped -- can be judged on the
# wall clock alone, and that has to clear the longest legitimate run or a
# healthy agent is failed mid-answer.
_UNRESPONSIVE_RUN_CUTOFF_SECONDS = 120
_ORPHANED_RUN_CUTOFF_SECONDS = AGENT_RUN_JOB_TIMEOUT_SECONDS + 300
_INTERRUPTED_RUN_ERROR = "Agent run was interrupted (worker restart or crash)"

# A live worker acts on a stop within a second, so one still pending after this
# means no worker ever will. STOP_REQUESTED holds the conversation's one active
# run slot, so until it settles a new message starts nothing and Retry refuses.
_STUCK_STOP_CUTOFF_SECONDS = 120


@streaq_cron("5-59/10 * * * *", name="reconcile_orphaned_agent_runs")
async def reconcile_orphaned_agent_runs() -> None:
    """Self-heal runs a worker abandoned, and stops nobody picked up.

    A worker shut down with SIGTERM relinquishes its task for the queue to
    redeliver, so what reaches here is what no worker got to hand back: SIGKILL,
    OOM, and the race where finalization lost to engine disposal. Those cannot
    be resumed safely -- nothing closed their outstanding tool calls -- so they
    fail terminally, publishing the same lifecycle and SSE events a normal
    finish does. Which runs those are is the job heartbeat's answer: on age
    alone this had to wait out the whole task timeout, and every message the
    person sent in those four hours joined the dead run and went unanswered.

    A run left STOP_REQUESTED is the other half, and settles as STOPPED: the
    user asked for it to end, and it is holding the conversation's active slot.
    """
    worker_ctx: AppWorkerContext = streaq_worker.context
    try:
        async with worker_ctx.uow() as uow:
            repo = ConversationRepository(uow)
            stale = await repo.list_stale_active_runs(
                cutoff_seconds=_ORPHANED_RUN_CUTOFF_SECONDS,
            )
            # The band the backstop cannot reach yet, decided by liveness.
            undecided = await repo.list_active_runs_pending_liveness(
                cutoff_seconds=_UNRESPONSIVE_RUN_CUTOFF_SECONDS,
                decided_after_seconds=_ORPHANED_RUN_CUTOFF_SECONDS,
            )
            gone = await dead_job_ids(agent_run_job_id(run.id) for run in undecided)
            stale.extend(run for run in undecided if agent_run_job_id(run.id) in gone)
            finalized: list[tuple[UUID, UUID, AgentRunStatus]] = []
            finalized.extend(
                await settle_stuck_stops(
                    repo, cutoff_seconds=_STUCK_STOP_CUTOFF_SECONDS
                )
            )
            for run in stale:
                finish_result = await repo.finish_agent_run(
                    agent_run_id=run.id,
                    status=AgentRunStatus.FAILED,
                    error=_INTERRUPTED_RUN_ERROR,
                )
                if finish_result is not None and finish_result.updated:
                    event_data = {"error": _INTERRUPTED_RUN_ERROR}
                    repo.collect_events(
                        [
                            AgentRunCompletedEvent(
                                conversation_id=run.conversation_id,
                                agent_run_id=run.id,
                                status=finish_result.status,
                                data=event_data,
                            )
                        ]
                    )
                    finalized.append(
                        (run.conversation_id, run.id, finish_result.status)
                    )
    except Exception:
        logger.error(
            "agent.handlers.reconcile_orphaned_agent_runs_cron.failed", exc_info=True
        )
        return

    if not finalized:
        return

    logger.debug(
        "agent.handlers.reconciled_d_orphaned_agent_run.diagnostic",
        count=len(finalized),
    )
    # Publish outside the UoW (mirrors handle_agent_control_event's stop path)
    # so SSE clients refresh and workflow waits resume promptly.
    for conversation_id, agent_run_id, status in finalized:
        event_data = {"error": _INTERRUPTED_RUN_ERROR}
        try:
            await publish_conversation_event(
                conversation_id,
                completed_payload(
                    conversation_id=conversation_id,
                    agent_run_id=agent_run_id,
                    status=status.value,
                    data=event_data,
                ),
            )
        except Exception:
            logger.error(
                "agent.handlers.publishing_reconciled_run_realtime_update.failed",
                agent_run_id=agent_run_id,
                exc_info=True,
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
