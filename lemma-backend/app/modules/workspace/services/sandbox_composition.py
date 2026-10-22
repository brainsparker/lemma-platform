"""Builds the sandbox service and the adapters that sit on it."""

from __future__ import annotations

import asyncio
from typing import Optional
from uuid import UUID

from app.modules.workspace.config import workspace_settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.workspace.contracts import SandboxInfo
from sandbox_runtime.errors import SandboxNotFound
from app.modules.workspace.domain.sandbox import (
    Sandbox,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.domain.workspace_identity import (
    is_pod_workspace_id,
    workspace_sandbox_id,
    workspace_slug,
)
from app.modules.workspace.services.host_loopback_policy import (
    is_local_host_users_browser_sandbox,
)
from app.modules.workspace.services.interfaces import ISandbox
from app.modules.workspace.services.local_sandbox_client import LocalSandboxClient
from app.modules.workspace.services.provider_factory import build_provider
from app.modules.workspace.services.sandbox_service import SandboxService

_service: SandboxService | None = None
_service_key: tuple[int, str] | None = None


def get_sandbox_service() -> SandboxService:
    """The process-shared sandbox service.

    Keyed by event loop as well as provider: the Docker client underneath owns
    an httpx.AsyncClient, which is bound to the loop that created it.
    """
    global _service, _service_key
    key = (id(asyncio.get_running_loop()), workspace_settings.provider)
    if _service is not None and _service_key == key:
        return _service
    _service = SandboxService(
        provider=build_provider(),
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        host_loopback=is_local_host_users_browser_sandbox,
    )
    _service_key = key
    return _service


async def reset_sandbox_service() -> None:
    global _service, _service_key
    service, _service = _service, None
    _service_key = None
    if service is not None:
        await service.close()


def build_local_client() -> LocalSandboxClient:
    return LocalSandboxClient(get_sandbox_service())


async def resolve_workspace(
    service: SandboxService,
    user_id: UUID,
    pod_id: UUID | None,
    *,
    actor_id: UUID | None = None,
) -> Sandbox:
    """The row for ``user_id``'s workspace in ``pod_id`` as ``actor_id`` (the
    pod's assistant when absent), made if absent.

    Owned by the person in every case, so plan sizing, the Desktop loopback
    policy and the process probe -- which all read the owner -- see a pod
    workspace as the person's, which it is.
    """
    sandbox_id = workspace_sandbox_id(user_id, pod_id, actor_id)
    return await service.resolve(
        kind=SandboxKind.WORKSPACE,
        owner_kind=SandboxOwnerKind.USER,
        owner_id=user_id,
        slug=workspace_slug(sandbox_id, pod_id),
        sandbox_id=sandbox_id,
    )


class LocalSandbox(ISandbox):
    """``ISandbox`` over the local sandbox service.

    A personal workspace's row is made on first use, because a user whose
    default workspace predates the backfill -- or was created after it -- still
    has to get one. A pod workspace's is made by `resolve_workspace`, which is
    the only place that knows whose it is.
    """

    name = "local"

    def __init__(self, service: SandboxService | None = None) -> None:
        self._service = service or get_sandbox_service()

    async def resolve_workspace(
        self, user_id: UUID, pod_id: UUID | None, *, actor_id: UUID | None = None
    ) -> UUID:
        return (
            await resolve_workspace(self._service, user_id, pod_id, actor_id=actor_id)
        ).id

    async def _sandbox_id(self, workspace_id: UUID) -> UUID:
        if is_pod_workspace_id(workspace_id):
            # Refused rather than made: from the id alone this would become a
            # workspace owned by a "user" that is a hash, which no sweep, plan
            # or policy could ever attribute to anybody.
            if await self._service.get(workspace_id) is None:
                raise SandboxNotFound(
                    "pod workspace has no row; resolve it through its owner first"
                )
            return workspace_id
        return (await resolve_workspace(self._service, workspace_id, None)).id

    async def ensure_sandbox(self, workspace_id: UUID) -> SandboxInfo:
        handle = await self._service.ensure(await self._sandbox_id(workspace_id))
        return _to_info(handle)

    async def get_sandbox(self, workspace_id: UUID) -> Optional[SandboxInfo]:
        """Report what exists. Never provisions.

        The caller uses this to decide whether an ensure is needed at all, so
        provisioning here would make every status read start a container.
        """
        return await self._service.describe(await self._sandbox_id(workspace_id))

    async def delete_sandbox(self, workspace_id: UUID) -> None:
        await self._service.destroy(await self._sandbox_id(workspace_id))

    async def suspend_sandbox(self, workspace_id: UUID) -> None:
        await self._service.release(await self._sandbox_id(workspace_id))

    async def is_sandbox_running(self, workspace_id: UUID) -> bool:
        info = await self.get_sandbox(workspace_id)
        return info is not None and info.status == "RUNNING"

    async def close(self) -> None:
        # The service is process-shared; it is disposed at lifespan shutdown.
        return None


def _to_info(handle) -> SandboxInfo:
    return SandboxInfo(
        sandbox_id=str(handle.sandbox_id),
        name=handle.provider_id,
        status="RUNNING",
        image="",
        created_at=None,
        endpoint=f"sandbox://{handle.sandbox_id}",
        # The epoch is what the old allocation pair meant: which incarnation of
        # this sandbox a caller is talking to.
        allocation_id=str(handle.sandbox_id),
        allocation_epoch=handle.epoch,
        storage_generation=handle.storage_generation,
    )
