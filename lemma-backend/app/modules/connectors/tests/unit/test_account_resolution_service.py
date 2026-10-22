from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.context import (
    Assistant,
    AuthorizationDecision,
    Context,
    Delegation,
    Person,
    PrincipalRef,
    ResourceType,
    Workload,
    WorkloadKind,
)
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_NAME,
    WorkloadPrincipalType,
)
from app.core.authorization.permissions import Permissions
from app.core.authorization.grants import connector_resource_id
from app.modules.connectors.domain.account import AccountEntity, OAuthCredentials
from app.modules.connectors.domain.errors import (
    AccountResolutionError,
    ConnectorAccessDeniedError,
)
from app.modules.connectors.services.account_resolution_service import (
    AccountResolutionService,
)

pytestmark = pytest.mark.asyncio


def _account(*, user_id, connector_id, account_id=None) -> AccountEntity:
    return AccountEntity(
        id=account_id or uuid4(),
        organization_id=uuid4(),
        user_id=user_id,
        connector_id=connector_id,
        auth_config_id=uuid4(),
        credentials=OAuthCredentials(access_token="token"),
    )


class _RecordingAuthorizer:
    """Answers every check one way and remembers what it was asked."""

    def __init__(self) -> None:
        self.allowed = True
        self.calls: list[tuple[str, object]] = []

    async def authorize(self, ctx, permission_id, resource=None):
        self.calls.append((permission_id, resource))
        return AuthorizationDecision(
            self.allowed,
            "TEST_ALLOW" if self.allowed else "MISSING_WORKLOAD_RESOURCE_GRANT",
            permission_id,
            resource,
        )

    async def accessible_resource_ids(self, *args, **kwargs):
        return frozenset()


def _user_actor(*, organization_id) -> Context:
    user_id = uuid4()
    return Context(
        actor=Person(user_id=user_id),
        actor_id=str(user_id),
        user_id=user_id,
        organization_id=organization_id,
        authorizer=_RecordingAuthorizer(),
    )


def _delegated_actor(
    *,
    user_id,
    pod_id,
    actor_id=None,
    actor_name="test-agent",
    is_default_pod_agent=False,
) -> Context:
    """The context ``build_delegated_workload_context`` returns, built for real.

    Every field here has to carry the value that builder would give it. A
    stand-in for it once capitalised the ``actor_id`` prefix, which production
    lowercases, and the service compared that prefix against the CAPS enum
    value -- so the double agreed with the bug and the pair of them passed.
    """
    resolved_actor_id = actor_id or uuid4()
    delegation = Delegation(name=actor_name)
    # The builder makes the pod's assistant an ``Assistant`` and every other
    # agent a named ``Workload``.
    actor = (
        Assistant(user_id=user_id, delegation=delegation)
        if is_default_pod_agent
        else Workload(
            kind=WorkloadKind.AGENT,
            workload_id=resolved_actor_id,
            for_user_id=user_id,
            principal_refs=frozenset(
                {PrincipalRef(WorkloadPrincipalType.AGENT.value, resolved_actor_id)}
            ),
            delegation=delegation,
        )
    )
    return Context(
        actor=actor,
        actor_id=f"{WorkloadPrincipalType.AGENT.value.lower()}:{resolved_actor_id}",
        user_id=user_id,
        pod_id=pod_id,
        authorizer=_RecordingAuthorizer(),
    )


async def test_resolve_account_returns_user_owned_account_for_plain_user():
    user_id = uuid4()
    account = _account(user_id=user_id, connector_id="slack")
    repo = AsyncMock(get_by_user_and_app=AsyncMock(return_value=account))
    service = AccountResolutionService(account_repository=repo)

    resolved = await service.resolve_account(
        user_id=user_id,
        connector_id="slack",
    )

    assert resolved.id == account.id


async def test_resolve_account_scopes_owned_lookup_to_context_org():
    # A plain-user resolution must look the account up within the caller's org,
    # not across every org the user belongs to.
    user_id = uuid4()
    org_id = uuid4()
    account = _account(user_id=user_id, connector_id="slack")
    repo = AsyncMock(
        get_by_user_org_and_app=AsyncMock(return_value=account),
        get_by_user_and_app=AsyncMock(
            return_value=_account(user_id=user_id, connector_id="slack")
        ),
    )
    service = AccountResolutionService(account_repository=repo)
    auth_actor = _user_actor(organization_id=org_id)

    resolved = await service.resolve_account(
        user_id=user_id,
        connector_id="slack",
        auth_actor=auth_actor,
    )

    assert resolved.id == account.id
    repo.get_by_user_org_and_app.assert_awaited_once_with(user_id, org_id, "slack")
    repo.get_by_user_and_app.assert_not_called()


async def test_resolve_account_rejects_explicit_account_from_another_org():
    user_id = uuid4()
    org_id = uuid4()
    # account.organization_id is a different (random) org than the caller's.
    account = _account(user_id=user_id, connector_id="slack")
    repo = AsyncMock(get=AsyncMock(return_value=account))
    service = AccountResolutionService(account_repository=repo)
    auth_actor = _user_actor(organization_id=org_id)

    with pytest.raises(AccountResolutionError):
        await service.resolve_account(
            user_id=user_id,
            connector_id="slack",
            auth_actor=auth_actor,
            account_id=account.id,
        )


async def test_resolve_for_auth_config_uses_org_scoped_lookup():
    user_id = uuid4()
    org_id = uuid4()
    auth_config_id = uuid4()
    account = _account(user_id=user_id, connector_id="slack")
    repo = AsyncMock(
        get_by_user_org_and_auth_config=AsyncMock(return_value=account),
        get_by_user_and_auth_config=AsyncMock(
            return_value=_account(user_id=user_id, connector_id="slack")
        ),
    )
    service = AccountResolutionService(account_repository=repo)
    auth_actor = _user_actor(organization_id=org_id)

    resolved = await service.resolve_account_for_auth_config(
        user_id=user_id,
        connector_id="slack",
        auth_config_id=auth_config_id,
        auth_actor=auth_actor,
    )

    assert resolved.id == account.id
    repo.get_by_user_org_and_auth_config.assert_awaited_once_with(
        user_id, org_id, auth_config_id
    )
    repo.get_by_user_and_auth_config.assert_not_called()


async def test_resolve_for_auth_config_rejects_explicit_account_from_another_org():
    user_id = uuid4()
    org_id = uuid4()
    account = _account(user_id=user_id, connector_id="slack")
    repo = AsyncMock(get=AsyncMock(return_value=account))
    service = AccountResolutionService(account_repository=repo)
    auth_actor = _user_actor(organization_id=org_id)

    with pytest.raises(AccountResolutionError):
        await service.resolve_account_for_auth_config(
            user_id=user_id,
            connector_id="slack",
            auth_config_id=account.auth_config_id,
            auth_actor=auth_actor,
            account_id=account.id,
        )


async def test_workload_rejects_explicit_account_from_another_org():
    # A workload runs inside one pod/org; an explicit account from a different
    # org is out of scope even before grant checks would pass.
    user_id = uuid4()
    pod_id = uuid4()
    org_id = uuid4()
    account = _account(user_id=user_id, connector_id="slack")
    repo = AsyncMock(get=AsyncMock(return_value=account))
    service = AccountResolutionService(
        account_repository=repo,
        authz_read_port=AsyncMock(),
        authorization_service=AsyncMock(),
    )
    auth_actor = _delegated_actor(user_id=user_id, pod_id=pod_id)
    auth_actor.organization_id = org_id  # account.organization_id is a different org

    with pytest.raises(AccountResolutionError):
        await service.resolve_account(
            user_id=user_id,
            connector_id="slack",
            auth_actor=auth_actor,
            account_id=account.id,
        )


async def test_resolve_account_rejects_explicit_account_from_another_user():
    user_id = uuid4()
    account = _account(user_id=uuid4(), connector_id="slack")
    repo = AsyncMock(get=AsyncMock(return_value=account))
    service = AccountResolutionService(account_repository=repo)

    with pytest.raises(AccountResolutionError):
        await service.resolve_account(
            user_id=user_id,
            connector_id="slack",
            account_id=account.id,
        )


async def test_resolve_account_uses_agent_owned_workload_account_from_another_user():
    user_id = uuid4()
    pod_id = uuid4()
    account = _account(user_id=uuid4(), connector_id="slack")
    authz_read_port = AsyncMock()
    repo = AsyncMock(get=AsyncMock(return_value=account))
    authorization_service = AsyncMock()
    service = AccountResolutionService(
        account_repository=repo,
        authz_read_port=authz_read_port,
        authorization_service=authorization_service,
    )
    auth_actor = _delegated_actor(user_id=user_id, pod_id=pod_id)

    resolved = await service.resolve_account(
        user_id=user_id,
        connector_id="slack",
        auth_actor=auth_actor,
        account_id=account.id,
    )

    assert resolved.id == account.id
    calls = auth_actor.authorizer.calls
    assert len(calls) == 2
    app_call, account_call = calls
    assert app_call[0] == Permissions.CONNECTOR_USE
    assert app_call[1].resource_type == ResourceType.CONNECTOR
    assert app_call[1].resource_id == connector_resource_id("slack")
    assert account_call[0] == Permissions.CONNECTOR_ACCOUNT_USE
    assert account_call[1].resource_type == ResourceType.CONNECTOR_ACCOUNT
    assert account_call[1].resource_id == account.id
    authz_read_port.get_pod_connector_id_by_alias.assert_not_called()


async def test_resolve_account_uses_explicit_current_user_workload_account():
    user_id = uuid4()
    pod_id = uuid4()
    account = _account(user_id=user_id, connector_id="slack")
    authz_read_port = AsyncMock()
    repo = AsyncMock(get=AsyncMock(return_value=account))
    authorization_service = AsyncMock()
    service = AccountResolutionService(
        account_repository=repo,
        authz_read_port=authz_read_port,
        authorization_service=authorization_service,
    )
    auth_actor = _delegated_actor(user_id=user_id, pod_id=pod_id)

    resolved = await service.resolve_account(
        user_id=user_id,
        connector_id="slack",
        auth_actor=auth_actor,
        account_id=account.id,
    )

    assert resolved.id == account.id
    assert len(auth_actor.authorizer.calls) == 1
    authz_read_port.get_pod_connector_id_by_alias.assert_not_called()


async def test_resolve_account_uses_user_owned_workload_account():
    user_id = uuid4()
    pod_id = uuid4()
    account = _account(user_id=user_id, connector_id="slack")
    authz_read_port = AsyncMock()
    repo = AsyncMock(get_by_user_and_app=AsyncMock(return_value=account))
    authorization_service = AsyncMock()
    service = AccountResolutionService(
        account_repository=repo,
        authz_read_port=authz_read_port,
        authorization_service=authorization_service,
    )
    auth_actor = _delegated_actor(user_id=user_id, pod_id=pod_id)

    resolved = await service.resolve_account(
        user_id=user_id,
        connector_id="slack",
        auth_actor=auth_actor,
    )

    assert resolved.id == account.id
    assert len(auth_actor.authorizer.calls) == 1
    authz_read_port.get_pod_connector_id_by_alias.assert_not_called()


async def test_resolve_account_rejects_user_account_without_workload_connector_access():
    user_id = uuid4()
    pod_id = uuid4()
    auth_actor = _delegated_actor(user_id=user_id, pod_id=pod_id)
    auth_actor.authorizer.allowed = False
    repo = AsyncMock()
    service = AccountResolutionService(
        account_repository=repo,
        authz_read_port=AsyncMock(),
        authorization_service=AsyncMock(),
    )

    with pytest.raises(ConnectorAccessDeniedError):
        await service.resolve_account(
            user_id=user_id,
            connector_id="slack",
            auth_actor=auth_actor,
        )

    repo.get_by_user_and_app.assert_not_called()


async def test_resolve_account_rejects_explicit_user_account_without_workload_connector_access():
    user_id = uuid4()
    pod_id = uuid4()
    account = _account(user_id=user_id, connector_id="slack")
    auth_actor = _delegated_actor(user_id=user_id, pod_id=pod_id)
    auth_actor.authorizer.allowed = False
    repo = AsyncMock(get=AsyncMock(return_value=account))
    service = AccountResolutionService(
        account_repository=repo,
        authz_read_port=AsyncMock(),
        authorization_service=AsyncMock(),
    )

    with pytest.raises(ConnectorAccessDeniedError):
        await service.resolve_account(
            user_id=user_id,
            connector_id="slack",
            auth_actor=auth_actor,
            account_id=account.id,
        )

    repo.get.assert_not_called()


async def test_default_pod_agent_delegation_uses_explicit_user_account():
    user_id = uuid4()
    pod_id = uuid4()
    account = _account(user_id=user_id, connector_id="slack")
    repo = AsyncMock(get=AsyncMock(return_value=account))
    authz_read_port = AsyncMock()
    service = AccountResolutionService(
        account_repository=repo,
        authz_read_port=authz_read_port,
        authorization_service=AsyncMock(),
    )
    auth_actor = _delegated_actor(
        user_id=user_id,
        pod_id=pod_id,
        # The assistant's ``agents`` row id, which is its pod's.
        actor_id=pod_id,
        actor_name=DEFAULT_POD_AGENT_NAME,
        is_default_pod_agent=True,
    )

    resolved = await service.resolve_account(
        user_id=user_id,
        connector_id="slack",
        auth_actor=auth_actor,
        account_id=account.id,
    )

    assert resolved.id == account.id
    assert auth_actor.authorizer.calls == []
    authz_read_port.get_pod_connector_id_by_alias.assert_not_called()
