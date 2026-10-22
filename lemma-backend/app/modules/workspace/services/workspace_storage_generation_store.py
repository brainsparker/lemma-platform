"""Tracks which durable-disk generation a workspace session last saw.

This is all that survives of the old ``WorkspaceStateStore``. The lifecycle
status it also wrote (``mark_creating``/``mark_running``/``mark_error``/
``mark_stopped``) was write-only -- nothing ever read it back -- and the
distributed creation lock was superseded by the in-process singleflight in
``WorkspaceSandboxService.get_or_create_sandbox``.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis

# Baked into the key rather than passed in. Moving it orphans the markers
# written under the old segment, which is harmless by construction: a session
# that has never been seen returns False from `observe_storage_generation`, so
# the worst case is one missed "your files were recreated" notice, not a false
# alarm.
_RUNTIME_KEY_SEGMENT = "workspace"


class WorkspaceStorageGenerationStore:
    """Remembers the storage generation each session has already observed."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        key_prefix: str = "workspace:state:v1",
    ):
        self._redis = get_redis(url=redis_url or settings.redis_url)
        self._key_prefix = key_prefix

    def _seen_generation_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:{_RUNTIME_KEY_SEGMENT}:seen-generation:{session_id}"

    async def observe_storage_generation(
        self,
        *,
        session_id: str,
        generation: int,
        sandbox_id: UUID | None = None,
        unlabelled_sandbox_id: UUID | None = None,
        ttl_seconds: int = 30 * 24 * 60 * 60,
    ) -> bool:
        """Record the disk generation this session last saw.

        Returns True only when the disk has been recreated since this session
        last looked -- or is a different disk altogether, which to the session
        is the same loss. First sight returns False: a brand new session
        starting on a brand new workspace has lost nothing, and telling it
        otherwise is the same false alarm we are trying to remove.

        The disk is named by ``sandbox_id`` because a conversation that ran in
        a person's single workspace now runs in its pod's, starting again at
        generation 1. Compared by number alone that is "nothing changed", and
        the agent reads an empty directory as one it never wrote to. A marker
        written before the sandbox was recorded names none; it was written by
        the workspace that was then the only one, ``unlabelled_sandbox_id``.
        """

        key = self._seen_generation_key(session_id)
        previous = await self._redis.get(key)
        await self._redis.set(
            key,
            str(generation) if sandbox_id is None else f"{generation}@{sandbox_id}",
            ex=max(1, ttl_seconds),
        )
        if previous is None:
            return False
        if isinstance(previous, bytes):
            previous = previous.decode()
        seen_generation, _, seen_sandbox = str(previous).partition("@")
        if sandbox_id is not None:
            seen_on = seen_sandbox or (
                str(unlabelled_sandbox_id) if unlabelled_sandbox_id else ""
            )
            if seen_on and seen_on != str(sandbox_id):
                return True
        try:
            return generation > int(seen_generation)
        except TypeError, ValueError:
            return False

    async def close(self) -> None:
        # The client is shared process-wide; closing it here would break
        # every other component still using the same pool. Disposal is
        # close_redis_clients()'s job at lifespan shutdown.
        self._redis = None
