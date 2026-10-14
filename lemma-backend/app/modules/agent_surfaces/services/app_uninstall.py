"""Stand a surface's account down when the platform takes its app away.

Slack sends ``app_uninstalled`` (or ``tokens_revoked`` for the bot) and then
nothing: every later send is refused, and until this ran the surface still read
"Connected and working". Marking the account ``REAUTH_REQUIRED`` is what the
rest of Lemma already understands -- the surface's setup turns into "Reinstall
it in Slack", and reconnecting repairs the account in place.

Scoped by the receivers the signature check resolved, never by the workspace:
one Slack workspace holds Lemma's app and any number of an org's own under the
same team id, and only the app that was removed signed this event.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfaceIngressRequest,
)
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.domain.surface_connectors import (
    SURFACE_CONNECTOR_BINDINGS,
)
from app.modules.connectors.contracts.retirement import retire_accounts

logger = get_logger(__name__)


async def retire_uninstalled_app(
    uow: SqlAlchemyUnitOfWork,
    *,
    surfaces_repo: SurfaceInstallationRepositoryPort,
    request: SurfaceIngressRequest,
    parsed: ParsedSurfaceLifecycleEvent,
) -> int:
    """Mark the accounts the departed app's bot ran on. Returns how many changed."""
    if parsed.kind is not SurfaceLifecycleKind.UNINSTALLED:
        return 0
    binding = SURFACE_CONNECTOR_BINDINGS.get(parsed.platform)
    if isinstance(request, SurfaceDirectWebhookIngress):
        receiver_ids = [request.surface_id]
    else:
        receiver_ids = request.receiver_surface_ids
    # No verified receivers means no proof which app this is: a fan-in would
    # stand down every app in the workspace for one app's departure.
    if binding is None or not receiver_ids:
        logger.info(
            "agent_surfaces.app_uninstalled.unscoped",
            platform=parsed.platform.value,
            tenant_id=parsed.tenant_id,
        )
        return 0
    surfaces = await surfaces_repo.list_active_for_routing(
        parsed.platform.value,
        surface_ids=receiver_ids,
        external_workspace_id=parsed.tenant_id,
    )
    revoked = parsed.revoked_identity_ids
    account_ids = {
        surface.account_id
        for surface in surfaces
        if surface.account_id is not None
        and (revoked is None or surface.surface_identity_id in revoked)
    }
    changed = await retire_accounts(
        uow.session, connector_id=binding.connector_id, account_ids=account_ids
    )
    await uow.commit()
    logger.info(
        "agent_surfaces.app_uninstalled.retired",
        platform=parsed.platform.value,
        tenant_id=parsed.tenant_id,
        surfaces=[str(surface.id) for surface in surfaces],
        accounts_changed=changed,
    )
    return changed
