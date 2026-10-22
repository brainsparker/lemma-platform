"""When a session is told its workspace's files are gone."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.workspace.services.workspace_storage_generation_store import (
    WorkspaceStorageGenerationStore,
)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        del ex
        self.values[key] = value


def _store() -> tuple[WorkspaceStorageGenerationStore, _Redis]:
    store = WorkspaceStorageGenerationStore.__new__(WorkspaceStorageGenerationStore)
    redis = _Redis()
    store._redis = redis
    store._key_prefix = "test"
    return store, redis


@pytest.mark.asyncio
async def test_a_conversation_moved_onto_its_pods_disk_is_told_its_files_are_gone() -> (
    None
):
    """Both disks are at generation 1, so by number alone nothing changed."""
    store, redis = _store()
    user_id, pod_disk = uuid4(), uuid4()
    # Written before markers named their sandbox: by the person's one workspace.
    redis.values[store._seen_generation_key("conversation")] = "1"

    moved = await store.observe_storage_generation(
        session_id="conversation",
        generation=1,
        sandbox_id=pod_disk,
        unlabelled_sandbox_id=user_id,
    )
    stayed = await store.observe_storage_generation(
        session_id="conversation",
        generation=1,
        sandbox_id=pod_disk,
        unlabelled_sandbox_id=user_id,
    )

    assert moved is True
    assert stayed is False


@pytest.mark.asyncio
async def test_an_unlabelled_marker_on_the_personal_disk_is_not_a_false_alarm() -> None:
    store, redis = _store()
    user_id = uuid4()
    redis.values[store._seen_generation_key("conversation")] = "1"

    assert not await store.observe_storage_generation(
        session_id="conversation",
        generation=1,
        sandbox_id=user_id,
        unlabelled_sandbox_id=user_id,
    )
    assert await store.observe_storage_generation(
        session_id="conversation",
        generation=2,
        sandbox_id=user_id,
        unlabelled_sandbox_id=user_id,
    )
