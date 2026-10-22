"""One person's workspaces, one per pod, against a real database.

Real because what keeps two pods apart is two rows and two containers, and the
rows are made by the unique index this schema ships with.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from sandbox_runtime.errors import SandboxNotFound
from app.modules.workspace.domain.sandbox import SandboxKind, SandboxOwnerKind
from app.modules.workspace.domain.workspace_identity import workspace_sandbox_id
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.services.sandbox_composition import LocalSandbox
from app.modules.workspace.services.sandbox_service import SandboxService
from app.modules.workspace.tests.integration.test_sandbox_service import FakeProvider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def service(provider: FakeProvider, sandbox_uow_factory) -> SandboxService:
    SandboxService._inflight.clear()
    SandboxService._recent.clear()
    return SandboxService(provider=provider, uow_factory=sandbox_uow_factory)


async def test_a_persons_pods_and_personal_workspace_are_three_containers(
    service: SandboxService, provider: FakeProvider
) -> None:
    sandbox = LocalSandbox(service)
    user_id, pod_a, pod_b = uuid4(), uuid4(), uuid4()

    in_a = await sandbox.resolve_workspace(user_id, pod_a)
    in_b = await sandbox.resolve_workspace(user_id, pod_b)
    personal = await sandbox.resolve_workspace(user_id, None)

    assert len({in_a, in_b, personal}) == 3
    assert personal == user_id
    for workspace_id in (in_a, in_b, personal):
        await sandbox.ensure_sandbox(workspace_id)
    assert len({spec.sandbox_id for spec in provider.created}) == 3
    # And each has a disk of its own: nothing written in one pod is there to
    # be run by another's shell.
    assert set(provider.volumes) == {in_a, in_b, personal}


async def test_a_pod_workspace_belongs_to_the_person(
    service: SandboxService, sandbox_uow_factory
) -> None:
    """Owned by the person, so plan sizing, the Desktop loopback policy and the
    process probe -- which all read the owner -- treat it as theirs."""
    user_id, pod_id = uuid4(), uuid4()

    workspace_id = await LocalSandbox(service).resolve_workspace(user_id, pod_id)
    # Resolving again is the same row, not a second one.
    assert await LocalSandbox(service).resolve_workspace(user_id, pod_id) == (
        workspace_id
    )

    async with sandbox_uow_factory() as uow:
        owned = await SandboxRepository(uow).list_for_owner(
            kind=SandboxKind.WORKSPACE,
            owner_kind=SandboxOwnerKind.USER,
            owner_id=user_id,
        )
    assert [row.id for row in owned] == [workspace_id]
    assert owned[0].slug == f"ws-{workspace_id.hex}"


async def test_a_named_agent_gets_a_container_and_disk_apart_from_the_assistant(
    service: SandboxService, provider: FakeProvider
) -> None:
    sandbox = LocalSandbox(service)
    user_id, pod_id, named = uuid4(), uuid4(), uuid4()

    assistant = await sandbox.resolve_workspace(user_id, pod_id)
    # The assistant's own row id is its pod's id; it is the same workspace.
    assert await sandbox.resolve_workspace(user_id, pod_id, actor_id=pod_id) == (
        assistant
    )
    agent = await sandbox.resolve_workspace(user_id, pod_id, actor_id=named)

    assert agent != assistant
    await sandbox.ensure_sandbox(assistant)
    await sandbox.ensure_sandbox(agent)
    assert len({spec.sandbox_id for spec in provider.created}) == 2
    assert set(provider.volumes) == {assistant, agent}


async def test_a_pod_workspace_is_never_made_from_its_id_alone(
    service: SandboxService, provider: FakeProvider
) -> None:
    """Made from the id, it would be owned by a "user" that is a hash."""
    unresolved = workspace_sandbox_id(uuid4(), uuid4())

    with pytest.raises(SandboxNotFound):
        await LocalSandbox(service).ensure_sandbox(unresolved)
    assert provider.created == []
