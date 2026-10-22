"""Pod connectors, for the modules that run things through them.

Surfaces, schedules, the sandbox's git bridge and pod bundle publishing each
name a pod connector by its slug and ask here for the account behind it. None
of them names an account: which account a pod connector means is decided by
its mode and by the account owner's sharing, in
`services/pod_connector_resolution.py`, and nowhere else.

Two ways to ask:

- ``resolve_pod_connector(uow, ctx, ...)`` -- somebody is acting now. The same
  checks as the execute route: ``connector.use`` on the connector, the account
  half for a SHARED one, the acting person's own account for a PER_MEMBER one.
- ``resolve_standing_pod_connector(uow, ...)`` -- a binding a person made
  earlier, running with nobody at the keyboard (a surface answering a message,
  a trigger subscription renewing). It re-reads the binding's standing on
  every use, so withdrawing a shared account stops it at once.

Both raise the typed errors in `domain/pod_connector_errors.py`; callers pass
them on as data rather than translating them.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.account import AccountEntity
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
from app.modules.connectors.domain.errors import (
    AccountNotFoundError,
    ConnectorDomainError,
)
from app.modules.connectors.services.pod_connector_resolution import (
    ResolvedPodConnector,
    acting_person,
)


@dataclass(frozen=True, slots=True)
class PodConnectorAccount:
    """A pod connector and the account it resolved to, for one use."""

    pod_id: UUID
    slug: str
    mode: PodConnectorMode
    pod_account_id: UUID
    owner_user_id: UUID
    auth_config_id: UUID
    connector_id: str
    #: The account itself, credentials included: the caller is about to use
    #: them. Secrets on it stay ``SecretStr`` until the point of use.
    account: AccountEntity

    @property
    def account_id(self) -> UUID:
        return self.account.id


def _resolver(uow: SqlAlchemyUnitOfWork):
    # Imported here, not at module scope: the dependency builders reach the
    # whole connectors service layer, which a contract importer should not pay
    # for until it resolves something.
    from app.modules.connectors.api.dependencies import build_pod_connector_resolver

    return build_pod_connector_resolver(uow)


def _present(resolved: ResolvedPodConnector) -> PodConnectorAccount:
    return PodConnectorAccount(
        pod_id=resolved.pod_connector.pod_id,
        slug=resolved.pod_connector.slug,
        mode=resolved.pod_connector.mode,
        pod_account_id=resolved.pod_account.id,
        owner_user_id=resolved.pod_account.owner_user_id,
        auth_config_id=resolved.auth_config.id,
        connector_id=resolved.auth_config.connector_id,
        account=resolved.account,
    )


async def resolve_pod_connector(
    uow: SqlAlchemyUnitOfWork, ctx: Context, *, pod_id: UUID, slug: str
) -> PodConnectorAccount:
    """The account ``slug`` runs as for whoever ``ctx`` is, authorized."""
    resolver = _resolver(uow)
    connector = await resolver.connector(pod_id, slug)
    return _present(await resolver.resolve(ctx, connector))


async def resolve_standing_pod_connector(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    slug: str,
    on_behalf_of: UUID | None = None,
    record_use: bool = True,
) -> PodConnectorAccount:
    """The account a standing binding to ``slug`` runs as right now.

    ``record_use=False`` reads it with no write: for a caller on a request
    path that releases its connection between reads (a surface answering a
    webhook inside a provider's deadline), where stamping ``last_used_at`` on
    every message would hold the connection across the provider call.
    """
    resolver = _resolver(uow)
    connector = await resolver.connector(pod_id, slug)
    return _present(
        await resolver.resolve_standing(
            connector, on_behalf_of=on_behalf_of, record_use=record_use
        )
    )


async def get_pod_connector(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, slug: str
) -> PodConnectorEntity | None:
    """The declaration alone, with no account resolved and nothing checked."""
    from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
        PodConnectorRepository,
    )

    return await PodConnectorRepository(uow).get_by_slug(pod_id, slug)


async def share_own_account_as_pod_connector(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    account_id: UUID,
    owner_user_id: UUID,
    slug_base: str,
    purpose: str | None = None,
) -> str:
    """Share an account its owner just connected *for this pod*, as a connector.

    For the flows where the owner's own action in the pod is what made the
    account -- Lemma creating a bot for a surface the person is setting up.
    That action is the consent sharing needs; nothing else may call this on
    somebody else's account, which is why it takes the owner and refuses an
    account that is not theirs.

    Returns the slug of the SHARED connector that runs as the account: the one
    already pointing at it, or a new ``slug_base`` (``-2``, ``-3`` when taken).
    """
    resolver = _resolver(uow)
    account = await resolver.accounts.get(account_id)
    if account is None or account.user_id != owner_user_id:
        raise AccountNotFoundError(str(account_id))
    pod_accounts = resolver.pod_accounts
    pod_connectors = resolver.pod_connectors
    pod_account = await pod_accounts.add(
        PodAccountEntity(
            pod_id=pod_id,
            account_id=account.id,
            auth_config_id=account.auth_config_id,
            owner_user_id=owner_user_id,
            visibility=PodAccountVisibility.POD,
            source=PodAccountSource.MANUAL,
        )
    )
    if not pod_account.is_shared:
        pod_account = await pod_accounts.set_visibility(
            pod_account.id, PodAccountVisibility.POD
        )
    for existing in await pod_connectors.list_by_shared_accounts([pod_account.id]):
        return existing.slug
    taken = {c.slug for c in await pod_connectors.list_by_pod(pod_id)}
    slug = slug_base
    n = 2
    while slug in taken:
        slug = f"{slug_base}-{n}"
        n += 1
    created = await pod_connectors.create(
        PodConnectorEntity(
            pod_id=pod_id,
            slug=slug,
            auth_config_id=account.auth_config_id,
            mode=PodConnectorMode.SHARED,
            shared_pod_account_id=pod_account.id,
            purpose=purpose,
            created_by_user_id=owner_user_id,
        )
    )
    return created.slug


async def shared_connectors_running_as(
    uow: SqlAlchemyUnitOfWork, account_ids: list[UUID]
) -> dict[UUID, list[tuple[UUID, str]]]:
    """``(pod_id, slug)`` of every SHARED connector currently running as each
    account, keyed by account id.

    For the questions a binding by slug asks about an account rather than a
    connector: is this bot already bound somewhere, does anything still use
    this grant. Only connectors whose account is shared right now count; one
    whose owner withdrew it runs as nothing.
    """
    from sqlalchemy import select

    from app.modules.connectors.infrastructure.models.pod_connector import (
        PodAccount,
        PodConnector,
    )

    if not account_ids:
        return {}
    rows = await uow.session.execute(
        select(PodAccount.account_id, PodConnector.pod_id, PodConnector.slug)
        .join(PodAccount, PodAccount.id == PodConnector.shared_pod_account_id)
        .where(
            PodAccount.account_id.in_(account_ids),
            PodAccount.visibility != PodAccountVisibility.PERSONAL.value,
            PodConnector.mode == PodConnectorMode.SHARED.value,
        )
    )
    found: dict[UUID, list[tuple[UUID, str]]] = {}
    for account_id, pod_id, slug in rows.all():
        found.setdefault(account_id, []).append((pod_id, slug))
    return found


async def shared_account_ids(
    uow: SqlAlchemyUnitOfWork, refs: list[tuple[UUID, str]]
) -> dict[tuple[UUID, str], UUID]:
    """The account each ``(pod_id, slug)`` SHARED connector runs as right now.

    A read for listings and routing narrowing, with no side effect and no
    error: a reference that resolves to nothing (no such connector, not
    SHARED, account withdrawn) is simply absent. Anything about to *use* the
    account goes through ``resolve_standing_pod_connector`` instead, which
    says why it cannot.
    """
    from sqlalchemy import select, tuple_

    from app.modules.connectors.infrastructure.models.pod_connector import (
        PodAccount,
        PodConnector,
    )

    if not refs:
        return {}
    rows = await uow.session.execute(
        select(PodConnector.pod_id, PodConnector.slug, PodAccount.account_id)
        .join(PodAccount, PodAccount.id == PodConnector.shared_pod_account_id)
        .where(
            tuple_(PodConnector.pod_id, PodConnector.slug).in_(list(set(refs))),
            PodAccount.visibility != PodAccountVisibility.PERSONAL.value,
            PodConnector.mode == PodConnectorMode.SHARED.value,
        )
    )
    return {(pod_id, slug): account_id for pod_id, slug, account_id in rows.all()}


#: What every refusal above is an instance of, for a caller that treats "this
#: connector runs as nothing right now" as one case.
PodConnectorRefusal = ConnectorDomainError

__all__ = [
    "ConnectorDomainError",
    "ConnectionRequiredError",
    "ConnectionRequiresPersonError",
    "ConnectorNeedsAccountError",
    "ConnectorUnavailableError",
    "PodConnectorAccount",
    "PodConnectorMode",
    "PodConnectorNotFoundError",
    "PodConnectorRefusal",
    "ReauthRequiredError",
    "acting_person",
    "get_pod_connector",
    "resolve_pod_connector",
    "resolve_standing_pod_connector",
    "share_own_account_as_pod_connector",
    "shared_account_ids",
    "shared_connectors_running_as",
]
