"""May this person make a schedule listen through this pod connector?

A WEBHOOK schedule on a connector trigger or an MCP server's event names a pod
connector, never an account. Binding it is the person's own use of that
connector, so it is asked exactly as a connector operation is asked
(`resolve_pod_connector`): ``connector.use`` on the connector; for a SHARED one
the account half too; for a PER_MEMBER one the person's own account, which the
pod picks up on this first use. Its typed refusals -- CONNECTION_REQUIRED with
its connect link, CONNECTOR_NEEDS_ACCOUNT, ... -- reach the caller as they are.

Asked at creation, and again of whoever takes a schedule over by changing what
it does (`schedule_ownership.py`). What keeps it running afterwards is the
connector's standing, re-read as the owner on every use (`resolve_as_owner`):
subscribing, matching a delivery, reconciling a subscription.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.authorization.context import Context
from app.core.log.log import get_logger
from app.modules.connectors.contracts.pod_connectors import (
    ConnectorDomainError,
    PodConnectorAccount,
    resolve_pod_connector,
    resolve_standing_pod_connector,
)
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import (
    ScheduleCreateEntity,
    ScheduleEntity,
    ScheduleType,
)

logger = get_logger(__name__)

#: ``(uow, ctx, *, pod_id, slug)``: resolves as ``ctx`` or raises.
ConnectorResolve = Callable[..., Awaitable[object]]


async def require_connector_use(
    schedule: ScheduleCreateEntity | ScheduleEntity,
    ctx: Context | None,
    uow: object,
    *,
    resolve: ConnectorResolve = resolve_pod_connector,
) -> None:
    """Refuse a binding ``ctx`` could not make by using the connector itself."""
    slug = schedule.pod_connector_slug
    if slug is None:
        return
    if schedule.schedule_type is not ScheduleType.WEBHOOK:
        raise ScheduleValidationError(
            "Only a webhook schedule listens through a pod connector."
        )
    if schedule.pod_id is None:
        raise ScheduleValidationError(
            "A schedule listens through a pod connector of its own pod."
        )
    if ctx is None:
        raise RuntimeError("Context is required to bind a schedule to a connector")
    await resolve(uow, ctx, pod_id=schedule.pod_id, slug=slug)


async def resolve_as_owner(
    uow: object, schedule: ScheduleEntity
) -> PodConnectorAccount:
    """What ``schedule``'s pod connector means right now, as its owner (§4.6).

    Raises the connector's typed refusal when it no longer resolves.
    """
    if schedule.pod_id is None or schedule.pod_connector_slug is None:
        raise ScheduleValidationError(
            "A schedule listens through a pod connector of its own pod."
        )
    return await resolve_standing_pod_connector(
        uow,
        pod_id=schedule.pod_id,
        slug=schedule.pod_connector_slug,
        on_behalf_of=schedule.user_id,
    )


async def standing_account(uow: object, schedule: ScheduleEntity) -> UUID | None:
    """The account ``schedule`` listens through now, or ``None`` if none.

    For the paths that decide whether to act at all -- a delivery to match, a
    subscription to keep -- where a connector that stopped resolving is an
    answer, not an error.
    """
    try:
        return (await resolve_as_owner(uow, schedule)).account_id
    except ConnectorDomainError as exc:
        logger.info(
            "schedule.connector.unresolved",
            schedule_id=str(schedule.id),
            code=getattr(exc, "code", type(exc).__name__),
        )
        return None


__all__ = [
    "ConnectorResolve",
    "require_connector_use",
    "resolve_as_owner",
    "standing_account",
]
