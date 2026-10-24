"""The deprecated org-level execute: an installation, maybe an account.

Code written before pod connectors names an installation and sometimes an
``account_id``. It is still served -- through the connectors the caller's pod
declares for that installation, with the same checks as the pod route -- and
deleted with the route once nothing calls it (see
``contracts/deprecations.py``). Its own module so the retirement is one file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.context import Context, Person
from app.modules.connectors.domain.execution_plan import ResolvedConnectorExecution
from app.modules.connectors.domain.pod_connector_errors import (
    ConnectorUnavailableError,
    PodConnectorNotFoundError,
)

if TYPE_CHECKING:
    from app.modules.connectors.services.connector_operation_service import (
        ConnectorOperationService,
    )


async def resolve_installation_execution(
    service: ConnectorOperationService,
    ctx: Context,
    *,
    user_id: UUID,
    organization_id: UUID,
    auth_config_name: str,
    pod_id: UUID | None,
    account_id: UUID | None,
    operation_name: str,
    payload: dict[str, object],
) -> ResolvedConnectorExecution:
    """Resolve the call through pod connectors, or the person's own account.

    With a pod (from the caller's token, or the body), the installation is
    reached through a connector that pod declares for it. A person may still
    use their own account where there is no pod, or the pod declares no
    connector for the installation -- which is what main allowed them.
    """
    if service.connector_service is None:
        raise ConnectorUnavailableError(slug=auth_config_name)
    auth_config = await service.connector_service.get_auth_config_by_name(
        user_id=user_id,
        organization_id=organization_id,
        auth_config_name=auth_config_name,
    )
    resolver = service.pod_connector_resolver
    account = None
    if pod_id is not None:
        try:
            account = (
                await resolver.serve_installation(
                    ctx, pod_id=pod_id, auth_config=auth_config, account_id=account_id
                )
            ).account
        except PodConnectorNotFoundError:
            if not isinstance(ctx.actor, Person):
                raise
    if account is None:
        account = await resolver.own_account(ctx, auth_config, account_id)
    return await service.execution_for(
        ctx,
        auth_config=auth_config,
        account=account,
        operation_name=operation_name,
        payload=payload,
        act_as="user",
    )
