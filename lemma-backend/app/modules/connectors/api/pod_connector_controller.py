"""A pod's connectors and the accounts in it, over HTTP.

Everything an operation runs through is under ``/pods/{pod_id}``: the pod
context pins a workload's token to its own pod there, so an agent or function
reaches its pod's connectors and nothing else, and no route outside a pod has
to be opened to workloads for connectors to work. The org-level execute route
these replace is gone.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.core.api.actor_policy import require_person
from app.core.api.dependencies import CurrentUser
from app.core.authorization.dependencies import PodContextDep
from app.modules.connectors.api.dependencies import (
    ConnectorOperationServiceDep,
    ConnectorOperationUseCasesDep,
    PodConnectorCoverageServiceDep,
    PodConnectorServiceDep,
)
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationDetail,
    OperationDiscoverResponse,
    OperationExecutionRequest,
    OperationExecutionResponse,
)
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    AccountPodUsageResponse,
    AskMembersRequest,
    AskMembersResponse,
    InstallationImpactResponse,
    PodAccountCreateRequest,
    PodAccountListResponse,
    PodAccountResponse,
    PodAccountUpdateRequest,
    PodConnectorCreateRequest,
    PodConnectorListResponse,
    PodConnectorResponse,
    PodConnectorUpdateRequest,
)

router = APIRouter(prefix="/pods/{pod_id}", tags=["Connectors"])
me_router = APIRouter(prefix="/me/connectors", tags=["Connectors"])
installation_router = APIRouter(
    prefix="/organizations/{organization_id}/installations", tags=["Connectors"]
)

#: Sharing a credential, or putting one in the pod, is a person's own act.
_PERSON_ONLY = require_person("share or add a connected account")


@router.get(
    "/connectors",
    response_model=PodConnectorListResponse,
    operation_id="pod.connector.list",
    summary="List Pod Connectors",
)
async def list_pod_connectors(
    pod_id: UUID,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
    coverage: PodConnectorCoverageServiceDep,
) -> PodConnectorListResponse:
    items = await service.list_connectors(ctx, pod_id)
    return PodConnectorListResponse(
        items=await coverage.with_coverage(ctx, pod_id, items)
    )


@router.post(
    "/connectors",
    response_model=PodConnectorResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="pod.connector.create",
    summary="Declare A Pod Connector",
)
async def create_pod_connector(
    pod_id: UUID,
    body: PodConnectorCreateRequest,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
) -> PodConnectorResponse:
    return await service.create_connector(ctx, pod_id, body)


@router.get(
    "/connectors/{slug}",
    response_model=PodConnectorResponse,
    operation_id="pod.connector.get",
    summary="Get Pod Connector",
)
async def get_pod_connector(
    pod_id: UUID,
    slug: str,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
    coverage: PodConnectorCoverageServiceDep,
) -> PodConnectorResponse:
    found = await service.get_connector(ctx, pod_id, slug)
    return (await coverage.with_coverage(ctx, pod_id, [found]))[0]


@router.patch(
    "/connectors/{slug}",
    response_model=PodConnectorResponse,
    operation_id="pod.connector.update",
    summary="Update Pod Connector",
)
async def update_pod_connector(
    pod_id: UUID,
    slug: str,
    body: PodConnectorUpdateRequest,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
) -> PodConnectorResponse:
    return await service.update_connector(ctx, pod_id, slug, body)


@router.delete(
    "/connectors/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="pod.connector.delete",
    summary="Remove Pod Connector",
)
async def delete_pod_connector(
    pod_id: UUID, slug: str, ctx: PodContextDep, service: PodConnectorServiceDep
) -> None:
    await service.delete_connector(ctx, pod_id, slug)


@router.post(
    "/connectors/{slug}/ask-members",
    response_model=AskMembersResponse,
    operation_id="pod.connector.ask_members",
    summary="Ask Members To Connect",
    description=(
        'Post one line, "This pod uses <connector> — connect yours: '
        '<connect_url>", in the own channel of each member a PER_MEMBER '
        "connector is waiting on. A member is asked about one connector at "
        "most once a day; those asked already are returned as `asked_recently`."
    ),
    dependencies=[require_person("ask members to connect")],
)
async def ask_members_to_connect(
    pod_id: UUID,
    slug: str,
    ctx: PodContextDep,
    coverage: PodConnectorCoverageServiceDep,
    body: AskMembersRequest | None = None,
) -> AskMembersResponse:
    return await coverage.ask_members(
        ctx, pod_id, slug, user_ids=body.user_ids if body else None
    )


@router.get(
    "/connectors/{slug}/operations",
    response_model=OperationDiscoverResponse,
    operation_id="pod.connector.operation.list",
    summary="Discover Pod Connector Operations",
)
async def list_pod_connector_operations(
    pod_id: UUID,
    slug: str,
    ctx: PodContextDep,
    service: ConnectorOperationServiceDep,
    query: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> OperationDiscoverResponse:
    return await service.pod_connector_operations(
        ctx, pod_id=pod_id, slug=slug, query=query, limit=limit
    )


@router.get(
    "/connectors/{slug}/operations/{operation_name}",
    response_model=OperationDetail,
    operation_id="pod.connector.operation.get",
    summary="Get Pod Connector Operation",
)
async def get_pod_connector_operation(
    pod_id: UUID,
    slug: str,
    operation_name: str,
    ctx: PodContextDep,
    service: ConnectorOperationServiceDep,
) -> OperationDetail:
    return await service.pod_connector_operation(
        ctx, pod_id=pod_id, slug=slug, operation_name=operation_name
    )


@router.post(
    "/connectors/{slug}/operations/{operation_name}/execute",
    response_model=OperationExecutionResponse,
    operation_id="pod.connector.operation.execute",
    summary="Execute Pod Connector Operation",
    description=(
        "Run an operation through a pod connector. The account it runs as is "
        "decided by the connector: the caller's own for a PER_MEMBER connector, "
        "the shared one for a SHARED connector. Typed refusals: "
        "`CONNECTION_REQUIRED` and `REAUTH_REQUIRED` (with `connect_url`), "
        "`CONNECTOR_NEEDS_ACCOUNT`, `CONNECTOR_UNAVAILABLE`."
    ),
)
async def execute_pod_connector_operation(
    pod_id: UUID,
    slug: str,
    operation_name: str,
    body: OperationExecutionRequest,
    request: Request,
    user: CurrentUser,
    use_cases: ConnectorOperationUseCasesDep,
) -> OperationExecutionResponse:
    # No request-scoped context: the use case authorizes and resolves in a
    # short scope of its own and makes the provider call holding no pooled
    # connection.
    return await use_cases.execute_pod_connector_operation(
        pod_id=pod_id,
        slug=slug,
        operation_name=operation_name,
        payload=body.payload,
        user_id=user.id,
        request=request,
    )


@router.get(
    "/connector-accounts",
    response_model=PodAccountListResponse,
    operation_id="pod.connector_account.list",
    summary="List Accounts In Use In The Pod",
)
async def list_pod_accounts(
    pod_id: UUID, ctx: PodContextDep, service: PodConnectorServiceDep
) -> PodAccountListResponse:
    return PodAccountListResponse(items=await service.list_accounts(ctx, pod_id))


@router.post(
    "/connector-accounts",
    response_model=PodAccountResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="pod.connector_account.add",
    summary="Add One Of Your Accounts To The Pod",
    dependencies=[_PERSON_ONLY],
)
async def add_pod_account(
    pod_id: UUID,
    body: PodAccountCreateRequest,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
) -> PodAccountResponse:
    return await service.add_account(
        ctx,
        pod_id,
        account_id=body.account_id,
        visibility=body.visibility,
        share_with=body.share_with,
    )


@router.patch(
    "/connector-accounts/{pod_account_id}",
    response_model=PodAccountResponse,
    operation_id="pod.connector_account.update",
    summary="Share Or Withdraw Your Account In The Pod",
    dependencies=[_PERSON_ONLY],
)
async def update_pod_account(
    pod_id: UUID,
    pod_account_id: UUID,
    body: PodAccountUpdateRequest,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
) -> PodAccountResponse:
    return await service.update_account(
        ctx,
        pod_id,
        pod_account_id,
        visibility=body.visibility,
        share_with=body.share_with,
        is_member_default=body.is_member_default,
        allow_sandbox_use=body.allow_sandbox_use,
    )


@router.delete(
    "/connector-accounts/{pod_account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="pod.connector_account.remove",
    summary="Remove An Account From The Pod",
)
async def remove_pod_account(
    pod_id: UUID,
    pod_account_id: UUID,
    ctx: PodContextDep,
    service: PodConnectorServiceDep,
) -> None:
    await service.remove_account(ctx, pod_id, pod_account_id)


@me_router.get(
    "/accounts/{account_id}/pods",
    response_model=AccountPodUsageResponse,
    operation_id="connector.account.pods",
    summary="Where One Of My Accounts Is Used",
)
async def account_pod_usage(
    account_id: UUID, user: CurrentUser, service: PodConnectorServiceDep
) -> AccountPodUsageResponse:
    return AccountPodUsageResponse(
        account_id=account_id,
        items=await service.where_used(user_id=user.id, account_id=account_id),
    )


@installation_router.get(
    "/{auth_config_id}/impact",
    response_model=InstallationImpactResponse,
    operation_id="connector.installation.impact",
    summary="What Removing An Installation Would Stop",
    description=(
        "Accounts, pod accounts and pod connectors on the installation, and the "
        "surfaces, schedules and MCP event subscriptions that run through them: "
        "organization-wide counts, and per pod for the pods the caller can open."
    ),
)
async def installation_impact(
    organization_id: UUID,
    auth_config_id: UUID,
    user: CurrentUser,
    service: PodConnectorServiceDep,
) -> InstallationImpactResponse:
    # Imported here: it reaches the surfaces, schedules and pods contracts,
    # which nothing else on a connector route needs.
    from app.modules.connectors.services.installation_impact import (
        with_what_else_stops,
    )

    impact = await service.impact(
        user_id=user.id,
        organization_id=organization_id,
        auth_config_id=auth_config_id,
    )
    return await with_what_else_stops(service.uow, impact, user_id=user.id)
