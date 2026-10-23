"""Exporting a pod's apps into a bundle's ``apps/`` directory.

Split out of :mod:`exporter` on the grounds :mod:`exporter_agents` gives. An app
travels as three things: its manifest, its code (source to rebuild, or the
built dist when there is no source), and its grants -- what its page may do
for whoever views it. An app imported without its grants is refused every call
it was built to make (``APP_NOT_GRANTED``), so they are not optional extras.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from lemma_pod_bundle.layout import _write_json
from lemma_pod_bundle.normalize import (
    _attach_permissions_payload,
    _normalize_app_payload,
)

from app.core.authorization.context import Context
from app.core.concurrency.offload import run_blocking
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_bundle.infrastructure.exporter_agents import GrantsPayload

if TYPE_CHECKING:
    from app.modules.pod_bundle.infrastructure.exporter import _ByteBudget

#: The grantee type an app's grants are stored under (``apps.services.app_grants``).
APP_GRANTEE = "APP"


async def export_apps(
    uow: SqlAlchemyUnitOfWork,
    *,
    root: Path,
    pod_id: UUID,
    user_id: UUID,
    ctx: Context,
    grants_payload: GrantsPayload,
    warnings: list[str],
    byte_budget: "_ByteBudget",
) -> None:
    """Write one directory per app: manifest with its grants, then its code."""
    # Imported here rather than at module load: `exporter` imports this module.
    from app.modules.apps.contracts.provisioning import list_app_names, require_app

    from app.modules.pod_bundle.infrastructure.exporter import _app_response_dict

    for app_name in sorted(
        await list_app_names(uow, pod_id=pod_id, user_id=user_id, ctx=ctx)
    ):
        app = await require_app(
            uow, pod_id=pod_id, name=app_name, user_id=user_id, ctx=ctx
        )
        dir_ = root / "apps" / app_name
        dir_.mkdir(parents=True, exist_ok=True)
        payload = _normalize_app_payload(_app_response_dict(app))
        grantee_id = getattr(app, "id", None)
        if grantee_id is not None:
            grants = await grants_payload(
                uow,
                pod_id=pod_id,
                grantee_type=APP_GRANTEE,
                grantee_id=grantee_id,
                warnings=warnings,
                grantee_name=app_name,
            )
            # Attach even an EMPTY grant list -- see _resource_grants_payload
            # for why None differs from [].
            if grants is not None:
                payload = _attach_permissions_payload(payload, grants)
        _write_json(dir_ / f"{app_name}.json", payload)
        # The app's source code is the critical payload: without it a re-import
        # gets an empty app. Download source (rebuildable) or, for widget/no-
        # source apps, the built dist -- mirrors the CLI's _download_app_assets
        # so an API export carries app code too.
        await _export_app_assets(
            uow,
            pod_id=pod_id,
            app_name=app_name,
            user_id=user_id,
            dest=dir_,
            ctx=ctx,
            byte_budget=byte_budget,
        )


async def _export_app_assets(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    app_name: str,
    user_id: UUID,
    dest: Path,
    ctx: Context,
    byte_budget: "_ByteBudget",
) -> None:
    """Export source and dist, prioritizing source within the byte budget.

    Source enables rebuilding for another pod; dist preserves the deployed
    build and is the fallback when source is unavailable. Match the CLI's
    ``_download_app_assets`` layout without claiming Vite build portability.
    """
    from app.modules.apps.contracts import AppNotFoundError
    from app.modules.apps.contracts.provisioning import (
        read_app_archive,
        resolve_app_dist_archive,
        resolve_app_source_archive,
    )

    from app.modules.pod_bundle.infrastructure.exporter import _extract_zip_bytes

    source_bytes: bytes | None = None
    try:
        app_id, source_path = await resolve_app_source_archive(
            uow, pod_id=pod_id, name=app_name, user_id=user_id, ctx=ctx
        )
        source_bytes = await read_app_archive(
            uow, app_id=app_id, archive_path=source_path
        )
    except AppNotFoundError:
        source_bytes = None

    if source_bytes and byte_budget.allow(
        name=f"apps/{app_name}/source", size=len(source_bytes)
    ):
        await run_blocking(
            _extract_zip_bytes,
            source_bytes,
            dest / "source",
            limiter="cpu_bound",
        )

    dist_bytes: bytes | None = None
    try:
        app_id, dist_path = await resolve_app_dist_archive(
            uow, pod_id=pod_id, name=app_name, user_id=user_id, ctx=ctx
        )
        dist_bytes = await read_app_archive(uow, app_id=app_id, archive_path=dist_path)
    except AppNotFoundError:
        dist_bytes = None

    if dist_bytes and byte_budget.allow(
        name=f"apps/{app_name}/dist.zip", size=len(dist_bytes)
    ):
        await run_blocking(
            (dest / "dist.zip").write_bytes, dist_bytes, limiter="cpu_bound"
        )
