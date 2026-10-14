"""What the old event transport could still owe, and who converts it.

The old transport delivered each outbox event to every consumer group that
read its stream, and each consumer recorded what it had finished in
``domain_event_inbox`` under its own name. So what is *owed* is readable in
SQL: an event is owed to consumer ``C`` while its outbox row is not dead and
``C`` has no settled inbox row for it -- whether or not the row was published.
The outbox, not Redis, is the source of truth for that:

* every event was written to the outbox in the transaction that raised it,
  before anything reached Redis (``uow._stage_pending_events``; ingress went
  through the same insert);
* a published row is kept for ``EVENT_COMPLETED_RETENTION_DAYS`` (7) after
  ``published_at``, and a consumer's COMPLETED inbox row for the same time
  after ``completed_at``, which is later -- so while a group could still
  deliver an entry, its row is still here to say whether it was delivered;
* a stream entry older than that was already lost to the old transport too
  (the gap-replay window is the same retention).

What Redis alone held is the streaq job queue: some consumers finished their
inbox row by *enqueueing* a job that did the real work. Those jobs are
converted from Redis at the end of the cutover (``STREAQ_ROUTES``).

Each **route** is one old inbox consumer whose work did not move to work
seeded from domain state. Consumers that did -- file processing, function and
agent resumes of workflow waits, a started or stop-requested agent run, pod
bundle jobs -- have no route: the migrations that moved them (0055-0062) seed
their items from the rows themselves. The memory-brief cache invalidation had
no inbox and a TTL backstop; the runtime-lifecycle consumer did nothing.

A module registers a converter per route (``legacy_converter``): given the
old event exactly as the consumer received it, it writes what the new code
writes when that event happens -- the same items, keyed the same way.
``cutover.legacy_event`` runs it (``work.py``); the sweep decides what is owed
(``takeover.py``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.infrastructure.work import JobContext

#: The inbox ``last_error_type`` of a delivery this cutover took over.
TAKEN_OVER_MARK = "work_runtime_cutover"
#: The mark the release before wrote for consumers it had already moved.
SETTLED_BY_WORK_MARK = "work_runtime"


@dataclass(frozen=True, slots=True)
class LegacyRoute:
    #: The old ``inbox.process`` consumer name.
    consumer: str
    #: Event types it acted on (exact).
    types: tuple[str, ...] = ()
    #: Or a ``LIKE`` pattern (record events).
    like: str | None = None
    #: Extra SQL on the outbox row ``o`` that the old consumer checked before
    #: claiming its inbox -- without it, "no inbox row" would not mean "owed".
    where: str | None = None


_POD_ID = "(o.payload->>'pod_id')"

#: Record events: the old consumers dropped a pod's events before claiming the
#: inbox when nothing in the pod watched them.
_POD_HAS_DATASTORE_TRIGGER = (
    "EXISTS (SELECT 1 FROM schedules s WHERE s.pod_id::text = "
    f"{_POD_ID} AND s.schedule_type = 'DATASTORE' AND s.is_active)"
)
_POD_HAS_MCP_SUBSCRIPTION = (
    "EXISTS (SELECT 1 FROM mcp_event_subscriptions m WHERE m.pod_id::text = "
    f"{_POD_ID} AND m.stopped_at IS NULL)"
)

ROUTES: tuple[LegacyRoute, ...] = (
    # pod events
    LegacyRoute("pod.join-request-email", ("pod.join_request.requested",)),
    LegacyRoute("datastore.pod-schema-bootstrap", ("pod.created",)),
    LegacyRoute("schedule-pod-events", ("pod.deleted",)),
    LegacyRoute("agent-surfaces.pod-deletion", ("pod.deleted",)),
    LegacyRoute("agent.pod-links.pod-deletion", ("pod.deleted",)),
    LegacyRoute("mcp-access.pod-deletion", ("pod.deleted",)),
    LegacyRoute("agent-surfaces.pod-member-removed", ("pod.member.removed",)),
    # schedules and workflows
    LegacyRoute("schedule-notifications", ("schedule.deactivated",)),
    LegacyRoute("schedule.workflow-outcomes", ("workflow.run.terminal",)),
    LegacyRoute("schedule.agent-outcomes", ("agent.run.completed",)),
    LegacyRoute("workflow.schedule-start", ("schedule.fired",)),
    # record changes
    LegacyRoute(
        "schedule-datastore-events",
        like="datastore.record.%",
        where=_POD_HAS_DATASTORE_TRIGGER,
    ),
    LegacyRoute(
        "mcp-access.record-created",
        ("datastore.record.insert",),
        where=_POD_HAS_MCP_SUBSCRIPTION,
    ),
    # agents
    LegacyRoute("agent.control", ("agent.run.completed",)),
    LegacyRoute("agent.notification_settled", ("notification.settled",)),
    # identity
    LegacyRoute(
        "identity-email-events",
        (
            "identity.user.signed_up",
            "identity.user.phone.replaced",
            "identity.organization.invitation.created",
            "identity.organization.invitation.accepted",
        ),
    ),
    LegacyRoute(
        "identity-mobile-verification-events",
        ("identity.mobile_verification.whatsapp.received",),
    ),
    # surfaces
    LegacyRoute("agent-surfaces.webhook", ("surface.webhook.received",)),
    LegacyRoute("agent-surfaces.onboarding", ("surface.onboarding.ready",)),
    LegacyRoute("agent-surfaces.identity", ("identity.user.mobile.changed",)),
    # product analytics (best effort today, but owed all the same)
    LegacyRoute(
        "analytics.agent",
        ("agent.created", "agent.conversation.started", "agent.run.completed"),
    ),
    LegacyRoute("analytics.app", ("app.created", "app.published")),
    LegacyRoute("analytics.connector", ("connector.connected",)),
    LegacyRoute(
        "analytics.datastore", ("datastore.table.created", "datastore.file.created")
    ),
    LegacyRoute("analytics.function", ("function.created",)),
    LegacyRoute(
        "analytics.identity",
        (
            "identity.user.signed_up",
            "identity.organization.created",
            "identity.organization.member_added",
        ),
    ),
    LegacyRoute("analytics.pod", ("pod.created", "pod.member.added", "pod.deleted")),
    LegacyRoute(
        "analytics.pod_bundle",
        ("pod_bundle.exported", "pod_bundle.import_completed"),
    ),
    LegacyRoute("analytics.schedule", ("schedule.created", "schedule.run.completed")),
    LegacyRoute("analytics.surface", ("surface.connected",)),
    LegacyRoute("analytics.workflow", ("workflow.created", "workflow.run.terminal")),
)

#: streaq jobs whose work existed only in Redis: converted, by function name,
#: through the converter registered as ``streaq:<function>``.
STREAQ_ROUTES: tuple[str, ...] = (
    "process_surface_message",
    "check_and_start_flows_for_schedule",
    "handle_llm_filter_task",
    "deliver_mcp_event",
    "cleanup_deleted_datastore_paths",
    "generate_conversation_title",
)
#: streaq jobs whose work is seeded from domain state (0055-0062, or the
#: owed-reply state route): their Redis copies are deleted, not converted.
STREAQ_SEEDED: tuple[str, ...] = (
    "process_agent_run",
    "reconcile_agent_approval",
    "process_function_run",
    "export_pod_bundle",
    "plan_pod_import",
    "import_pod_github",
    "import_pod_url",
    "apply_pod_import",
    "publish_pod_github",
    "process_datastore_file_task",
    "decide_workflow_step",
    "resume_workflow_run_for_function",
    "resume_workflow_run_for_agent",
    "deliver_owed_reply",
)

#: Work owed in domain state the old transport's crons or jobs settled, run
#: once at the end: each route's converter reads its own rows.
STATE_ROUTES: tuple[str, ...] = (
    "state:surfaces.owed_replies",
    "state:surfaces.notification_expiry",
    "state:schedule.dispatched_runs",
)

#: ``(ctx, event, route)``: the old event as its consumer received it, and
#: which route (old consumer) it is owed to.
type Converter = Callable[["JobContext", dict[str, Any], str], Awaitable[None]]

_converters: dict[str, Converter] = {}


def legacy_converter(*names: str) -> Callable[[Converter], Converter]:
    """Register ``fn`` as the converter of routes ``names``."""

    def register(fn: Converter) -> Converter:
        for name in names:
            existing = _converters.get(name)
            if existing is not None and existing is not fn:
                raise ValueError(f"route {name!r} already has a converter")
            _converters[name] = fn
        return fn

    return register


def converter_for(name: str) -> Converter | None:
    return _converters.get(name)


def all_route_names() -> tuple[str, ...]:
    return (
        *(route.consumer for route in ROUTES),
        *(f"streaq:{name}" for name in STREAQ_ROUTES),
        *STATE_ROUTES,
    )


__all__ = [
    "ROUTES",
    "SETTLED_BY_WORK_MARK",
    "STATE_ROUTES",
    "STREAQ_ROUTES",
    "STREAQ_SEEDED",
    "TAKEN_OVER_MARK",
    "Converter",
    "LegacyRoute",
    "all_route_names",
    "converter_for",
    "legacy_converter",
]
