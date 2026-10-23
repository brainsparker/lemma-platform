"""Applying an app's bundled grants: the deferred APP_GRANTS step.

Split from :mod:`applier` (at the file-size ceiling), like
:mod:`surface_apply`. The grants are set through the apps module's
``replace_app_grants`` -- the path the permissions route takes -- so an import
is held to the same rules as a person setting them by hand: ``app.update`` on
the app, and every grant one the importer could use themselves
(``CONFERRAL_EXCEEDS_HOLDER``). The plan asked that already, of everything that
existed when it was built (:mod:`grant_planning`); this asks again of
everything, including what the bundle created since.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_bundle.domain.state import PlanStep
from app.modules.pod_bundle.infrastructure.grants import (
    grants_from_payload,
    has_grants,
)


async def apply_app_grants(
    step: PlanStep,
    uow: SqlAlchemyUnitOfWork,
    ctx: Context,
    pod_id: UUID,
    user_id: UUID,
    load: Callable[[str, str], dict[str, object]],
) -> None:
    """Replace the app's grants with the bundle's; no ``permissions`` key
    leaves them alone, an empty list revokes them (``grants.has_grants``)."""
    from app.modules.apps.contracts.provisioning import replace_app_grants

    payload = load("apps", step.name)
    if not has_grants(payload):
        return
    await replace_app_grants(
        uow,
        ctx=ctx,
        pod_id=pod_id,
        app_name=step.name,
        grants=grants_from_payload(payload),
        user_id=user_id,
    )
