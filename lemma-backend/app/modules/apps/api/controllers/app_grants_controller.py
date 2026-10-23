"""An app's declared grants, over HTTP: what its page may do for its viewer.

See ``services/app_grants.py`` for the model. Split from ``app_controller.py``,
which is near the file-size ceiling.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.apps.api.dependencies import AppServiceDep
from app.modules.apps.api.schemas.app_schemas import (
    AppPermissionsReplaceRequest,
    AppPermissionsResponse,
    AppResourcePermissionResponse,
)
from app.modules.apps.domain.entities import AppEntity
from app.modules.apps.services.app_grants import named_app_grants, replace_app_grants

router = APIRouter(
    prefix="/pods/{pod_id}/apps",
    tags=["Apps"],
    redirect_slashes=False,
)

_GRANTS_DESCRIPTION = (
    "An app's page acts for whoever views it, with that viewer's access "
    "intersected with these grants and never more. A call outside them is "
    "refused `403 APP_NOT_GRANTED`, naming the resource type, name and "
    "permission to grant here."
)


async def apply_inline_app_grants(
    uow: SqlAlchemyUnitOfWork,
    *,
    ctx: Context,
    app: AppEntity,
    permissions: AppPermissionsReplaceRequest | None,
    user_id: UUID,
) -> None:
    """A create or update payload's ``permissions``: absent leaves the grants."""
    if permissions is None:
        return
    assert app.id is not None
    await replace_app_grants(
        uow.session,
        ctx=ctx,
        pod_id=app.pod_id,
        app_id=app.id,
        grants=permissions.grants,
        created_by_user_id=user_id,
    )


async def _permissions_response(
    uow: SqlAlchemyUnitOfWork, app: AppEntity
) -> AppPermissionsResponse:
    assert app.id is not None
    grouped = await named_app_grants(uow.session, pod_id=app.pod_id, app_id=app.id)
    return AppPermissionsResponse(
        app_id=app.id,
        app_name=app.name,
        grants=[
            AppResourcePermissionResponse(
                resource_type=resource_type,
                resource_name=resource_name,
                permission_ids=sorted(set(permission_ids)),
            )
            for (resource_type, resource_name), permission_ids in grouped.items()
        ],
    )


@router.get(
    "/{app_name}/permissions",
    response_model=AppPermissionsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="app.permissions.get",
    summary="Get App Grants",
    description=_GRANTS_DESCRIPTION,
)
async def get_app_permissions(
    pod_id: UUID,
    app_name: str,
    app_service: AppServiceDep,
    user: CurrentUser,
    uow: UoWDep,
    ctx: PodContextDep,
) -> AppPermissionsResponse:
    app = await app_service.get_app_by_name(
        pod_id, app_name, user.id, raise_not_found=True, ctx=ctx
    )
    assert app is not None
    return await _permissions_response(uow, app)


@router.put(
    "/{app_name}/permissions",
    response_model=AppPermissionsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="app.permissions.replace",
    summary="Replace App Grants",
    description=(
        _GRANTS_DESCRIPTION + " Replacing them needs `app.update` on the app, "
        "and every grant must be one you could use yourself on that resource "
        "(`CONFERRAL_EXCEEDS_HOLDER` otherwise)."
    ),
)
async def replace_app_permissions(
    pod_id: UUID,
    app_name: str,
    data: AppPermissionsReplaceRequest,
    app_service: AppServiceDep,
    user: CurrentUser,
    uow: UoWDep,
    ctx: PodContextDep,
) -> AppPermissionsResponse:
    app = await app_service.get_app_by_name(
        pod_id, app_name, user.id, raise_not_found=True, ctx=ctx
    )
    assert app is not None and app.id is not None
    await ctx.require(Permissions.APP_UPDATE, ResourceRef.app(pod_id, app.id))
    await apply_inline_app_grants(
        uow, ctx=ctx, app=app, permissions=data, user_id=user.id
    )
    return await _permissions_response(uow, app)
