"""The rest of what removing an installation stops: surfaces, schedules and
MCP event subscriptions, and the pods they are in.

``PodConnectorService.impact`` answers what is this module's own -- accounts,
pod accounts, pod connectors. Surfaces and schedules are other modules' rows,
bound to a pod connector by ``(pod, slug)``, so they are asked for through
those modules' contracts; nothing here reads their tables.

The counts are the organization's: whoever removes an installation removes it
for everyone, and should know how much goes. The pods are named only where the
caller could open them, asked of the caller's own authority in each, the way a
request of theirs to that pod would be.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from app.core.authorization.context import ResourceRef
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.contracts.connector_usage import (
    surfaces_on_pod_connectors,
)
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    ImpactedPod,
    InstallationImpactResponse,
)
from app.modules.connectors.infrastructure.repositories.installation_impact_queries import (
    event_subscription_count,
    event_subscription_ids,
)
from app.modules.pod.contracts.user_pods import pods_by_ids
from app.modules.schedule.contracts.connector_usage import (
    schedules_on_pod_connectors,
)

#: How many of each are read to say where they are. Past this the totals are
#: still exact for subscriptions; surfaces and schedules stop counting here.
MAX_IMPACT_ROWS = 2000

#: How many pods are checked for whether the caller can open them.
MAX_NAMED_PODS = 100

PodVisible = Callable[[SqlAlchemyUnitOfWork, UUID, UUID], Awaitable[bool]]


async def caller_can_open(
    uow: SqlAlchemyUnitOfWork, user_id: UUID, pod_id: UUID
) -> bool:
    """Whether a request of the caller's to this pod would be let in."""
    ctx = await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=pod_id
    )
    return await ctx.can(Permissions.POD_READ, ResourceRef.pod(pod_id))


async def with_what_else_stops(
    uow: SqlAlchemyUnitOfWork,
    impact: InstallationImpactResponse,
    *,
    user_id: UUID,
    can_open: PodVisible = caller_can_open,
) -> InstallationImpactResponse:
    connectors: Sequence[tuple[UUID, str]] = sorted(
        {(one.pod_id, one.slug) for one in impact.pod_connectors}
    )
    session = uow.session
    surfaces = await surfaces_on_pod_connectors(
        session, connectors, limit=MAX_IMPACT_ROWS
    )
    schedules = await schedules_on_pod_connectors(
        session, connectors, limit=MAX_IMPACT_ROWS
    )
    subscriptions = await event_subscription_ids(
        session, impact.auth_config_id, limit=MAX_IMPACT_ROWS
    )
    surfaces_in = Counter(one.pod_id for one in surfaces)
    schedules_in = Counter(one.pod_id for one in schedules)
    subscriptions_in = Counter(
        one.pod_id for one in schedules if one.provider_trigger_id in subscriptions
    )
    slugs_in: dict[UUID, list[str]] = defaultdict(list)
    for pod_id, slug in connectors:
        slugs_in[pod_id].append(slug)
    candidates = sorted(set(slugs_in))[:MAX_NAMED_PODS]
    visible = [pod_id for pod_id in candidates if await can_open(uow, user_id, pod_id)]
    pods = [
        ImpactedPod(
            pod_id=pod.id,
            name=pod.name,
            pod_connectors=sorted(slugs_in[pod.id]),
            surface_count=surfaces_in[pod.id],
            schedule_count=schedules_in[pod.id],
            event_subscription_count=subscriptions_in[pod.id],
        )
        for pod in await pods_by_ids(session=session, pod_ids=visible)
    ]
    return impact.model_copy(
        update={
            "surface_count": len(surfaces),
            "schedule_count": len(schedules),
            "event_subscription_count": await event_subscription_count(
                session, impact.auth_config_id
            ),
            "pods": pods,
        }
    )


__all__ = ["MAX_IMPACT_ROWS", "caller_can_open", "with_what_else_stops"]
