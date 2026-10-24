"""A GitHub App installation, bound to the one Lemma organization it serves.

Audit H4. GitHub names an installation by a bare number and one App serves
every organization that installs it, so the number alone says nothing about
which Lemma tenant is entitled to it. ``verify_installation`` proves the
claimant's GitHub user can *see* the installation -- which a contractor with
read on one repository can -- and that used to be the whole check. The account
that carried the number then got the App's token on every repository in the
installation, and the installation's webhooks ran its organization's schedules.

So an installation is bound to an organization (``connector_installations``,
with ``connector_id = 'github'``), and everything that acts on one looks the
binding up:

- **claiming** -- the setup callback, reconciling an account, binding one by
  hand -- accepts an installation already bound to the claimant's
  organization, refuses one bound anywhere else, and binds an unbound one only
  with proof from GitHub that the claimant controls it
  (``github_installation.administers``): the installation is on their own
  GitHub user, or on a GitHub organization they are an admin of. The callback's
  organization and person come from its connect request: a 256-bit, single-use
  ``state`` stored server-side with both, completed only in that person's
  browser (H2). A forged state claims nothing; another organization's is
  refused before anything is exchanged.
- **minting** an App token, and **provisioning** a schedule's routing key, use
  an account's installation only while it is bound to the account's own
  organization (``routable_external_ref``);
- **webhooks** route a delivery only to schedules in the organization its
  installation is bound to (``bound_organization``), and drop an unbound one.

There is no unbound path. Migration 0063 binds every existing installation
whose organization can be derived and unbinds the accounts of the rest.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID, uuid7

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log.log import get_logger
from app.modules.connectors.domain.account import AccountEntity
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.github_installation_errors import (
    GithubInstallationBoundElsewhereError,
    GithubInstallationNotAdministeredError,
    InstallationClaimNotPermittedError,
)
from app.modules.connectors.infrastructure.adapters.organization_access import (
    SqlAlchemyOrganizationAccessAdapter,
)
from app.modules.connectors.infrastructure.models.connector_installation import (
    ConnectorInstallation,
)

logger = get_logger(__name__)

GITHUB = "github"


def is_github(connector_id: str | None) -> bool:
    return (connector_id or "").strip().lower() == GITHUB


async def bound_organization(
    session: AsyncSession, installation_id: str
) -> UUID | None:
    """The organization this installation is bound to, if any."""
    return await session.scalar(
        select(ConnectorInstallation.organization_id).where(
            ConnectorInstallation.connector_id == GITHUB,
            ConnectorInstallation.external_id == str(installation_id),
        )
    )


async def claimable_by(
    session: AsyncSession, organization_id: UUID, installation_ids: Iterable[str]
) -> set[str]:
    """Which of these an organization may bind: its own, and unbound ones."""
    wanted = {str(one) for one in installation_ids}
    if not wanted:
        return set()
    rows = await session.execute(
        select(
            ConnectorInstallation.external_id, ConnectorInstallation.organization_id
        ).where(
            ConnectorInstallation.connector_id == GITHUB,
            ConnectorInstallation.external_id.in_(wanted),
        )
    )
    elsewhere = {
        installation_id
        for installation_id, owner in rows.all()
        if owner != organization_id
    }
    return wanted - elsewhere


def _refused_elsewhere(organization_id: UUID) -> GithubInstallationBoundElsewhereError:
    logger.warning(
        "connectors.github_bindings.claim_refused.denied",
        organization_id=str(organization_id),
    )
    return GithubInstallationBoundElsewhereError()


async def _may_install(
    session: AsyncSession, user_id: UUID | None, organization_id: UUID
) -> bool:
    """Whether the claimant may install apps for the organization in Lemma."""
    if user_id is None:
        return False
    access = SqlAlchemyOrganizationAccessAdapter(SqlAlchemyUnitOfWork(session))
    return await access.user_holds_organization_permission(
        user_id, organization_id, Permissions.ORG_INSTALLATION_MANAGE
    )


async def claim_installation(
    session: AsyncSession,
    *,
    installation_id: str,
    organization_id: UUID,
    user_id: UUID | None,
    administered: bool,
) -> None:
    """Hold an installation for this organization, or refuse.

    Already this organization's: nothing to do, and no proof needed -- anyone
    in it may connect through an installation it holds. Another's: refused.
    Nobody's: bound here only if ``administered`` -- GitHub said the claimant
    controls it -- and the claimant may install apps for the organization in
    Lemma (``org.installation.manage``), and then by an insert that does nothing on conflict followed
    by a read of the winner, so two organizations racing for it cannot both
    believe they hold it.
    """
    owner = await bound_organization(session, installation_id)
    if owner == organization_id:
        return
    if owner is not None:
        raise _refused_elsewhere(organization_id)
    if not administered:
        logger.warning(
            "connectors.github_bindings.claim_unproven.denied",
            organization_id=str(organization_id),
        )
        raise GithubInstallationNotAdministeredError()
    if not await _may_install(session, user_id, organization_id):
        logger.warning(
            "connectors.github_bindings.claim_not_permitted.denied",
            organization_id=str(organization_id),
        )
        raise InstallationClaimNotPermittedError()
    inserted = await session.execute(
        insert(ConnectorInstallation)
        .values(
            id=uuid7(),
            connector_id=GITHUB,
            external_id=str(installation_id),
            organization_id=organization_id,
            bound_by_user_id=user_id,
        )
        .on_conflict_do_nothing(index_elements=["connector_id", "external_id"])
        .returning(ConnectorInstallation.id)
    )
    if inserted.scalar() is not None:
        logger.info(
            "connectors.github_bindings.installation_bound",
            organization_id=str(organization_id),
        )
        return
    if await bound_organization(session, installation_id) != organization_id:
        raise _refused_elsewhere(organization_id)


async def claim_for_account(
    session: AsyncSession,
    account: AccountEntity,
    installation_id: str | None,
    *,
    administered: bool,
) -> None:
    """``claim_installation`` for the organization and person an account is."""
    if not installation_id or not is_github(account.connector_id):
        return
    await claim_installation(
        session,
        installation_id=installation_id,
        organization_id=account.organization_id,
        user_id=account.user_id,
        administered=administered,
    )


async def release_installation(session: AsyncSession, installation_id: str) -> bool:
    """Let go of an installation GitHub says was uninstalled.

    Only on GitHub's own signed ``installation.deleted``: the installation no
    longer exists upstream, so whoever installs the App again -- the same
    organization or another -- claims it afresh, with proof. A suspended one
    keeps its binding: suspending is undone by unsuspending it, not by a new
    claim.
    """
    released = await session.execute(
        delete(ConnectorInstallation)
        .where(
            ConnectorInstallation.connector_id == GITHUB,
            ConnectorInstallation.external_id == str(installation_id),
        )
        .returning(ConnectorInstallation.id)
    )
    return released.scalar() is not None


class InstallationBindings:
    """The binding reads and the claim, over one session.

    What the reconciler is handed rather than reaching for module functions,
    so a test can stand in front of the database without patching the
    reconciler's own names.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def owner(self, installation_id: str) -> UUID | None:
        return await bound_organization(self._session, installation_id)

    async def claimable_by(
        self, organization_id: UUID, installation_ids: Iterable[str]
    ) -> set[str]:
        return await claimable_by(self._session, organization_id, installation_ids)

    async def claim(
        self, account: AccountEntity, installation_id: str, *, administered: bool
    ) -> None:
        await claim_for_account(
            self._session, account, installation_id, administered=administered
        )


async def routable_external_ref(
    session: AsyncSession, account: AccountEntity
) -> str | None:
    """The account's upstream tenant, as far as acting on it is concerned.

    For every connector but GitHub this is ``external_ref`` unchanged. For
    GitHub it is the installation only while that installation is bound to the
    account's own organization: an account still naming another organization's
    installation mints no App token for it and provisions no routing key on it.
    """
    ref = account.external_ref
    if not ref or not is_github(account.connector_id):
        return ref
    if await bound_organization(session, ref) == account.organization_id:
        return str(ref)
    logger.warning(
        "connectors.github_bindings.unbound_installation_ignored.denied",
        account_id=str(account.id),
    )
    return None


__all__ = [
    "InstallationBindings",
    "bound_organization",
    "claim_for_account",
    "claim_installation",
    "claimable_by",
    "is_github",
    "release_installation",
    "routable_external_ref",
]
