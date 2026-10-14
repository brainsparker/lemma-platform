"""The cutover's converters for the pod events the old transport still owed.

Each writes what ``infrastructure/pod_work.stage_pod_work`` writes today when
the event happens -- the join-request email, the ``PodCreated`` /
``PodDeleted`` / ``PodMemberRemoved`` fact -- restricted to the subscriber
that replaces the old consumer that still owed it, so a consumer that had
already done its part does not do it again. Worker only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.infrastructure.cutover.routes import legacy_converter
from app.core.infrastructure.work import JobContext, facts
from app.modules.pod.contracts.lifecycle import (
    PodCreated,
    PodDeleted,
    PodMemberRemoved,
)
from app.modules.pod.domain.events import PodJoinRequestedEvent
from app.modules.pod.events.join_request_work import add_join_request_email_work

#: Old consumer -> the subscriber kind that does its work now.
_DELETION_KINDS = {
    "schedule-pod-events": "schedule.pod_deleted",
    "agent-surfaces.pod-deletion": "surfaces.pod_deleted",
    "agent.pod-links.pod-deletion": "agent.pod_links.pod_deleted",
    "mcp-access.pod-deletion": "mcp_access.pod_deleted",
}


def _uuid(event: dict[str, Any], key: str) -> UUID | None:
    value = event.get(key)
    return UUID(str(value)) if value else None


@legacy_converter("pod.join-request-email")
async def join_request_email(
    ctx: JobContext, event: dict[str, Any], route: str
) -> None:
    requested = PodJoinRequestedEvent.model_validate(event)
    async with ctx.complete_work() as uow:
        await add_join_request_email_work(uow, requested)


@legacy_converter("datastore.pod-schema-bootstrap")
async def pod_created(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    fact = PodCreated(
        pod_id=UUID(str(event["pod_id"])),
        organization_id=UUID(str(event["organization_id"])),
    )
    async with ctx.complete_work() as uow:
        await facts.emit(uow, fact, only={"pod.provision"})


@legacy_converter(*_DELETION_KINDS)
async def pod_deleted(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    fact = PodDeleted(
        pod_id=UUID(str(event["pod_id"])),
        organization_id=UUID(str(event["organization_id"])),
        deleted_by_user_id=_uuid(event, "deleted_by_user_id"),
    )
    async with ctx.complete_work() as uow:
        await facts.emit(uow, fact, only={_DELETION_KINDS[route]})


@legacy_converter("agent-surfaces.pod-member-removed")
async def member_removed(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    fact = PodMemberRemoved(
        pod_id=UUID(str(event["pod_id"])), user_id=UUID(str(event["user_id"]))
    )
    async with ctx.complete_work() as uow:
        await facts.emit(uow, fact, only={"surfaces.member_removed"})
