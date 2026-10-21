"""An org's own provider app, registered by another module on the org's behalf.

The bring-your-own-app path has always ended in an ``ORG_CUSTOM`` install made
from credentials a person pasted. When Lemma creates the app itself -- a pod's
own Slack app, made with the org's configuration token -- the credentials come
back to the server, and this is how they become that same install without
passing through anybody's browser.

Goes through ``ConnectorService.create_auth_config`` rather than writing the row,
so the role check and the config validation are the ones every other install
gets. Unlike a hand-made install it is never made the org's default: it is one
teammate's app, and nobody chose it to answer a bare connector lookup.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import SecretStr

from app.modules.connectors.api.dependencies import get_connector_service
from app.modules.connectors.domain.auth_config import AuthConfigSource


async def may_manage_installs(uow, *, user_id: UUID, organization_id: UUID) -> bool:
    """Whether this person may add or remove the org's connector installs."""
    return await get_connector_service(uow).may_manage_installs(
        user_id=user_id, organization_id=organization_id
    )


async def create_own_app_install(
    uow,
    *,
    user_id: UUID,
    organization_id: UUID,
    connector_id: str,
    name: str,
    config: dict[str, str | SecretStr],
) -> UUID:
    """Register the org's own app as an install, and return the install's id.

    Commits: ``create_auth_config`` commits before validating and again after
    inserting, so a caller that fails afterwards undoes it with
    ``delete_unauthorised_install``, not a rollback.
    """
    install = await get_connector_service(uow).create_auth_config(
        user_id=user_id,
        organization_id=organization_id,
        connector_id=connector_id,
        config_source=AuthConfigSource.ORG_CUSTOM.value,
        # Revealed here, the one place the plaintext is needed: the install's
        # config is what the connectors module encrypts and stores.
        config={
            key: value.get_secret_value() if isinstance(value, SecretStr) else value
            for key, value in config.items()
        },
        name=name,
        is_default=False,
    )
    return install.id


async def install_is_authorised(uow, auth_config_id: UUID) -> bool:
    """Whether anybody has connected an account through this install yet."""
    accounts = await get_connector_service(uow).account_repository.list_by_auth_config(
        auth_config_id
    )
    return bool(accounts)


async def delete_unauthorised_install(
    uow,
    *,
    organization_id: UUID,
    auth_config_id: UUID | None = None,
    name: str | None = None,
) -> bool:
    """Remove an install nobody authorised, which there is nothing to revoke
    for. ``False`` when it is gone, belongs elsewhere, or has an account --
    an install somebody connected is never removed behind their back.

    By name when the id never reached the caller: ``create_own_app_install``
    commits before it returns, so a create interrupted there leaves an install
    its caller knows only by the name it asked for.
    """
    service = get_connector_service(uow)
    repository = service.auth_config_repository
    if auth_config_id is not None:
        install = await repository.get(auth_config_id)
    elif name is not None:
        install = await repository.get_active_by_org_and_name(organization_id, name)
    else:
        return False
    if install is None or install.organization_id != organization_id:
        return False
    if await service.account_repository.list_by_auth_config(install.id):
        return False
    return await repository.delete(install.id)
