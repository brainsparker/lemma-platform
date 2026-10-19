"""``surface.push_digest``: the pushes the hourly budget held, told as one.

Queued, delayed, by the first push the budget held for a member in a channel
(``services/push_digests``); pushes held meanwhile coalesce into it. FAST: it
reads the held deliveries, marks them told, and writes the member one note in
the same transaction, so a retried item finds nothing left to tell.
"""

from __future__ import annotations

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.modules.agent_surfaces.events.work_kinds import SURFACE_PUSH_DIGEST
from app.modules.agent_surfaces.services.push_digests import PushDigest, send_digest


async def push_digest(ctx: FastContext, payload: PushDigest) -> None:
    await send_digest(ctx.uow, payload)


def register_push_digest_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACE_PUSH_DIGEST,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.FAST,
        payload=PushDigest,
    )(push_digest)


register_push_digest_work(default_registry)
