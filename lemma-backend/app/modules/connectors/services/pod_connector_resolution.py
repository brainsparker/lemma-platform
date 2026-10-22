"""The one way an operation picks the account it runs as.

``resolve(ctx, pod_connector)`` is design §3.3. Every caller names a pod
connector and nothing else; the account follows from the connector's mode and
from who is acting:

- **SHARED**: the pod account the connector names, whoever acts. Two checks,
  ``connector.use`` on the connector and ``connector_account.use`` on the pod
  account -- the second is what a POD_VIEWER, or anyone a RESTRICTED account
  was not shared with, fails.
- **PER_MEMBER**: the acting person's own pod account for that installation.
  On their first call the pod picks up their default org account as a PERSONAL
  pod account, so the pod records which accounts it uses (R5).

It replaced ``AccountResolutionService``, whose account was an ``account_id``
argument any caller -- the model included -- could type, with authorization on
it inferred per calling pod from whether some grant happened to exist. Nothing
here takes an account id.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import (
    Context,
    Person,
    ResourceRef,
    Workload,
)
from app.core.authorization.permissions import Permissions
from app.core.authorization.workload_authority import invoking_person_context
from app.core.config import settings
from app.modules.connectors.domain.account import AccountEntity, AccountStatus
from app.modules.connectors.domain.auth_config import (
    AuthConfigEntity,
    AuthConfigStatus,
)
from app.modules.connectors.domain.errors import ConnectorAccessDeniedError
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodAccountSource,
    PodAccountVisibility,
    PodConnectorEntity,
    PodConnectorMode,
)
from app.modules.connectors.domain.pod_connector_errors import (
    ConnectionRequiredError,
    ConnectionRequiresPersonError,
    ConnectorNeedsAccountError,
    ConnectorUnavailableError,
    PodConnectorNotFoundError,
    ReauthRequiredError,
)
from app.modules.connectors.domain.ports import AccountRepositoryPort
from app.modules.connectors.infrastructure.repositories.auth_config_repository import (
    AuthConfigRepository,
)
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodAccountRepository,
    PodConnectorRepository,
)


@dataclass(frozen=True, slots=True)
class ResolvedPodConnector:
    pod_connector: PodConnectorEntity
    pod_account: PodAccountEntity
    account: AccountEntity
    auth_config: AuthConfigEntity


def connect_url(pod_id: UUID, slug: str) -> str:
    """Where a person connects (or reconnects) the account a connector needs.

    The pod's Connections page, opened on this connector, rather than the
    org-level connect flow: it is where the person was trying to work, and it
    is what returns them there once connected.
    """
    base = settings.frontend_url.rstrip("/")
    return f"{base}/pods/{pod_id}/connections?connector={slug}"


def acting_person(ctx: Context) -> UUID | None:
    """The person whose own account a PER_MEMBER call uses, if there is one.

    Themselves for a person; the person mirrored by the assistant or invoking
    a named agent or function. Nobody for a contact, an anonymous visitor,
    another pod or a run with no person behind it.
    """
    if isinstance(ctx.actor, Person):
        return ctx.actor.user_id
    return ctx.delegating_user_id


class PodConnectorResolver:
    def __init__(
        self,
        *,
        pod_connectors: PodConnectorRepository,
        pod_accounts: PodAccountRepository,
        auth_configs: AuthConfigRepository,
        accounts: AccountRepositoryPort,
    ):
        self.pod_connectors = pod_connectors
        self.pod_accounts = pod_accounts
        self.auth_configs = auth_configs
        self.accounts = accounts

    async def connector(self, pod_id: UUID, slug: str) -> PodConnectorEntity:
        found = await self.pod_connectors.get_by_slug(pod_id, slug)
        if found is None:
            raise PodConnectorNotFoundError(slug)
        return found

    async def resolve(
        self, ctx: Context, pod_connector: PodConnectorEntity
    ) -> ResolvedPodConnector:
        auth_config = await self.auth_configs.get(pod_connector.auth_config_id)
        if auth_config is None or auth_config.status is AuthConfigStatus.DISABLED:
            raise ConnectorUnavailableError(slug=pod_connector.slug)
        await ctx.require(
            Permissions.CONNECTOR_USE,
            ResourceRef.connector(pod_connector.pod_id, pod_connector.id),
        )
        if pod_connector.mode is PodConnectorMode.SHARED:
            pod_account = await self._shared_account(ctx, pod_connector)
        else:
            pod_account = await self._member_account(ctx, pod_connector, auth_config)
        return await self._resolved(
            pod_connector,
            pod_account,
            auth_config,
            acting_person_is_owner=acting_person(ctx) == pod_account.owner_user_id,
        )

    async def resolve_standing(
        self,
        pod_connector: PodConnectorEntity,
        *,
        on_behalf_of: UUID | None,
        record_use: bool = True,
    ) -> ResolvedPodConnector:
        """The account behind a binding that runs with nobody at the keyboard.

        A surface answering a message and a schedule's trigger subscription
        were bound by a person, through ``resolve`` with that person's own
        authority; what keeps them running afterwards is no caller's check but
        the standing of what they were bound to. So this re-reads that standing
        on every use, and fails the same typed way ``resolve`` does the moment
        it changes: the owner withdraws a shared account (NEEDS_ACCOUNT), the
        owner of a per-member binding has no account in the pod any more
        (left the pod, removed it: CONNECTION_REQUIRED), the installation is
        disabled, the account needs reauth.

        ``on_behalf_of`` is the person a PER_MEMBER binding runs for -- a
        schedule's owner. Nothing is auto-added here: a pod account appears on
        a person's own call, never on a background one.
        """
        auth_config = await self.auth_configs.get(pod_connector.auth_config_id)
        if auth_config is None or auth_config.status is AuthConfigStatus.DISABLED:
            raise ConnectorUnavailableError(slug=pod_connector.slug)
        if pod_connector.mode is PodConnectorMode.SHARED:
            pod_account = (
                await self.pod_accounts.get(pod_connector.shared_pod_account_id)
                if pod_connector.shared_pod_account_id is not None
                else None
            )
            if pod_account is None or not pod_account.is_shared:
                raise ConnectorNeedsAccountError(
                    slug=pod_connector.slug,
                    connect_url=connect_url(pod_connector.pod_id, pod_connector.slug),
                )
        else:
            if on_behalf_of is None:
                raise ConnectionRequiresPersonError(slug=pod_connector.slug)
            pod_account = await self.pod_accounts.get_member_default(
                pod_id=pod_connector.pod_id,
                owner_user_id=on_behalf_of,
                auth_config_id=pod_connector.auth_config_id,
            )
            if pod_account is None:
                raise ConnectionRequiredError(
                    slug=pod_connector.slug,
                    connector_id=auth_config.connector_id,
                    connect_url=connect_url(pod_connector.pod_id, pod_connector.slug),
                )
        return await self._resolved(
            pod_connector,
            pod_account,
            auth_config,
            acting_person_is_owner=on_behalf_of == pod_account.owner_user_id,
            record_use=record_use,
        )

    async def _resolved(
        self,
        pod_connector: PodConnectorEntity,
        pod_account: PodAccountEntity,
        auth_config: AuthConfigEntity,
        *,
        acting_person_is_owner: bool,
        record_use: bool = True,
    ) -> ResolvedPodConnector:
        account = await self.accounts.get(pod_account.account_id)
        if account is None:
            # Deleted between the two reads; the cascade will remove the pod
            # account too, which is the state this already is in.
            raise ConnectorNeedsAccountError(
                slug=pod_connector.slug,
                connect_url=connect_url(pod_connector.pod_id, pod_connector.slug),
            )
        if account.status is AccountStatus.REAUTH_REQUIRED:
            raise ReauthRequiredError(
                slug=pod_connector.slug,
                connector_id=auth_config.connector_id,
                connect_url=connect_url(pod_connector.pod_id, pod_connector.slug),
                acting_person_is_owner=acting_person_is_owner,
            )
        if record_use:
            await self.pod_accounts.touch(pod_account.id)
        return ResolvedPodConnector(
            pod_connector=pod_connector,
            pod_account=pod_account,
            account=account,
            auth_config=auth_config,
        )

    async def _shared_account(
        self, ctx: Context, pod_connector: PodConnectorEntity
    ) -> PodAccountEntity:
        pod_account = (
            await self.pod_accounts.get(pod_connector.shared_pod_account_id)
            if pod_connector.shared_pod_account_id is not None
            else None
        )
        if pod_account is None or not pod_account.is_shared:
            raise ConnectorNeedsAccountError(
                slug=pod_connector.slug,
                connect_url=connect_url(pod_connector.pod_id, pod_connector.slug),
            )
        await _require_account_use(ctx, pod_account)
        return pod_account

    async def _member_account(
        self,
        ctx: Context,
        pod_connector: PodConnectorEntity,
        auth_config: AuthConfigEntity,
    ) -> PodAccountEntity:
        person = acting_person(ctx)
        if person is None:
            raise ConnectionRequiresPersonError(slug=pod_connector.slug)
        existing = await self.pod_accounts.get_member_default(
            pod_id=pod_connector.pod_id,
            owner_user_id=person,
            auth_config_id=pod_connector.auth_config_id,
        )
        if existing is not None:
            return existing
        account = await self.accounts.get_by_user_org_and_auth_config(
            person, auth_config.organization_id, auth_config.id
        )
        if account is None:
            raise ConnectionRequiredError(
                slug=pod_connector.slug,
                connector_id=auth_config.connector_id,
                connect_url=connect_url(pod_connector.pod_id, pod_connector.slug),
            )
        added = await self.pod_accounts.add(
            PodAccountEntity(
                pod_id=pod_connector.pod_id,
                account_id=account.id,
                auth_config_id=account.auth_config_id,
                owner_user_id=account.user_id,
                visibility=PodAccountVisibility.PERSONAL,
                is_member_default=True,
                source=PodAccountSource.AUTO,
            )
        )
        if not added.is_member_default:
            # The account was already in the pod -- added by hand, or carried
            # over by the migration -- just not as the one these calls use.
            await self.pod_accounts.make_member_default(added)
            added = added.model_copy(update={"is_member_default": True})
        return added


async def _require_account_use(ctx: Context, pod_account: PodAccountEntity) -> None:
    """The account half of a SHARED call.

    A named agent or function is checked as the person behind it, not as the
    intersection: its grant is on the pod connector, never on an account (an
    agent is granted ``support-inbox``, and which account that means was
    decided by its owner). Asking the workload for a grant on the account too
    would bring back exactly the pinning this replaced.

    A run with no person behind it may use what its owner shared with the
    whole pod, and nothing narrower: there is nobody to check a RESTRICTED
    grant against.
    """
    ref = ResourceRef.connector_account(pod_account.pod_id, pod_account.id)
    if ctx.is_named_workload:
        person = invoking_person_context(ctx.authorizer, ctx)
        assert person is not None, "a named workload always has its person"
        await person.require(Permissions.CONNECTOR_ACCOUNT_USE, ref)
        return
    if isinstance(ctx.actor, Workload):
        if pod_account.visibility is PodAccountVisibility.POD:
            return
        raise ConnectorAccessDeniedError(
            "This shared account is restricted to some members, and this run "
            "has no member behind it.",
            details={"reason": "restricted_account_without_person"},
        )
    await ctx.require(Permissions.CONNECTOR_ACCOUNT_USE, ref)
