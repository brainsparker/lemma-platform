"""What a pod app may do for its viewer: its grants, and who may confer them.

An app's page is its own principal (``core/authorization/app_principal``,
audit H20): the viewer's access ∩ these grants. They are ordinary resource
grants with grantee ``APP`` -- the same rows, matcher and SQL projection an
agent's and a function's grants use -- so there is no second store to drift
from the one authorization reads. The app's *declared* grants (its manifest:
``table:metrics:read``, ``connector:telemetry:use``) are exactly these rows,
set through the app's ``permissions`` (on create, update, or the permissions
route).

Conferral (PS-ACCESS-010) is asked twice:

- **when grants are set**: whoever sets them must be able to do each one on
  that very resource (``assert_can_confer_grants``);
- **when the app is published or released**: the person putting code behind
  the grants -- uploading a bundle, promoting a release -- must hold them too.
  Otherwise an editor who cannot read a RESTRICTED table could ship code that
  reads it through every viewer who can.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.conferral import assert_can_confer_grants
from app.core.authorization.context import Context, ResourceType
from app.core.authorization.grants import (
    NormalizedResourceGrant,
    ResourceGrantInputProtocol,
    delete_grantee_grants,
    list_grantee_resource_grants,
    normalize_pod_resource_grants,
    replace_grantee_resource_grants,
    validate_pod_resource_grant_permissions,
)
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.domain.errors import BadRequestError
from app.core.authorization.resource_names import resolve_resource_names_by_ids

#: The grantee type an app's grants are stored under.
APP_GRANTEE = "APP"

#: Grant rows (resource × permission) one app may hold. Publishing checks every
#: one, so the read is bounded here and a larger set is refused, never cut
#: short: a conferral check that skipped rows would pass what it never saw.
MAX_APP_GRANT_ROWS = 2000


def _too_many_grants() -> BadRequestError:
    return BadRequestError(
        f"An app may hold at most {MAX_APP_GRANT_ROWS} grants (resource and "
        "permission pairs). Grant a folder rather than each file in it.",
        code="APP_GRANTS_LIMIT",
    )


async def app_grants(
    session: AsyncSession, *, pod_id: UUID, app_id: UUID
) -> list[NormalizedResourceGrant]:
    """The app's grants, one entry per resource."""
    rows = (
        await session.execute(
            select(
                ResourcePermissionGrantModel.resource_type,
                ResourcePermissionGrantModel.resource_id,
                ResourcePermissionGrantModel.permission_id,
            )
            .where(
                ResourcePermissionGrantModel.pod_id == pod_id,
                ResourcePermissionGrantModel.grantee_type == APP_GRANTEE,
                ResourcePermissionGrantModel.grantee_id == app_id,
            )
            .limit(MAX_APP_GRANT_ROWS + 1)
        )
    ).all()
    if len(rows) > MAX_APP_GRANT_ROWS:
        raise _too_many_grants()
    grouped: dict[tuple[str, UUID], list[str]] = {}
    for resource_type, resource_id, permission_id in rows:
        grouped.setdefault((resource_type, resource_id), []).append(permission_id)
    return [
        NormalizedResourceGrant(
            resource_type=ResourceType(resource_type),
            resource_id=resource_id,
            permission_ids=sorted(permission_ids),
        )
        for (resource_type, resource_id), permission_ids in sorted(
            grouped.items(), key=lambda item: (item[0][0], str(item[0][1]))
        )
    ]


async def assert_publisher_holds_app_grants(
    ctx: Context, session: AsyncSession, *, pod_id: UUID, app_id: UUID
) -> None:
    """Refuse to publish code behind grants the publisher could not use.

    A grant on a resource that is gone reaches nothing, and is not held against
    the publisher: grants carry no foreign key, so one can outlive its row.
    """
    grants = await app_grants(session, pod_id=pod_id, app_id=app_id)
    names = await resolve_resource_names_by_ids(
        session,
        pod_id=pod_id,
        refs=[(grant.resource_type, grant.resource_id) for grant in grants],
    )
    grants = [g for g in grants if (g.resource_type, g.resource_id) in names]
    if grants:
        await assert_can_confer_grants(
            ctx,
            grants,
            pod_id=pod_id,
            action="publish an app whose grants you could not use yourself",
        )


async def replace_app_grants(
    session: AsyncSession,
    *,
    ctx: Context,
    pod_id: UUID,
    app_id: UUID,
    grants: Sequence[ResourceGrantInputProtocol],
    created_by_user_id: UUID,
) -> None:
    """Replace the app's grants with ``grants``; an empty list revokes them all."""
    if sum(len(set(grant.permission_ids)) for grant in grants) > MAX_APP_GRANT_ROWS:
        raise _too_many_grants()
    validate_pod_resource_grant_permissions(grants)
    normalized = await normalize_pod_resource_grants(
        session, pod_id=pod_id, grants=grants
    )
    await assert_can_confer_grants(
        ctx,
        normalized,
        pod_id=pod_id,
        action="grant an app permissions you could not use",
    )
    await replace_grantee_resource_grants(
        session,
        pod_id=pod_id,
        grantee_type=APP_GRANTEE,
        grantee_id=app_id,
        grants=normalized,
        created_by_user_id=created_by_user_id,
    )


async def named_app_grants(
    session: AsyncSession, *, pod_id: UUID, app_id: UUID
) -> dict[tuple[ResourceType, str], list[str]]:
    """The app's grants keyed by ``(resource type, resource name)``."""
    return await list_grantee_resource_grants(
        session, pod_id=pod_id, grantee_type=APP_GRANTEE, grantee_id=app_id
    )


async def delete_app_grants(
    session: AsyncSession, *, pod_id: UUID, app_id: UUID
) -> None:
    """An app's grants go with it: nothing else would ever remove them."""
    await delete_grantee_grants(
        session, pod_id=pod_id, grantee_type=APP_GRANTEE, grantee_id=app_id
    )


__all__ = [
    "APP_GRANTEE",
    "app_grants",
    "assert_publisher_holds_app_grants",
    "delete_app_grants",
    "named_app_grants",
    "replace_app_grants",
]
