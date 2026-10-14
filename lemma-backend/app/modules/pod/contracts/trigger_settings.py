"""A pod's defaults for its DATASTORE triggers (``config.triggers``)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def pod_bulk_writes_default(session: AsyncSession, pod_id: UUID) -> str | None:
    """``triggers.bulk_writes_default`` -- ``skip`` | ``each`` | ``batch`` -- or ``None``."""
    from app.modules.pod.infrastructure.models import Pod

    value = await session.scalar(
        select(Pod.config["triggers"]["bulk_writes_default"].astext).where(
            Pod.id == pod_id
        )
    )
    return value if value in ("skip", "each", "batch") else None


__all__ = ["pod_bulk_writes_default"]
