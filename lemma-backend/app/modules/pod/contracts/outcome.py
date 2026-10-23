"""What a pod owns (``config.outcome``), for the modules that count it.

The pod module keeps the outcome; datastore counts where the pod stands
against it from the pod's own tables, and the agent says it in its brief.
Both read it here rather than reaching into the pod's config blob themselves.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pod.domain.pod_entities import (
    OutcomeChecklist,
    OutcomeKind,
    OutcomeMeasure,
    PodOutcome,
)


async def read_pod_outcome(session: AsyncSession, pod_id: UUID) -> PodOutcome | None:
    """The pod's outcome, or ``None`` when it owns none -- or when what is
    stored no longer reads as one, which is said as owning nothing rather than
    failing every read of the pod."""
    from app.modules.pod.infrastructure.models import Pod

    raw = await session.scalar(select(Pod.config["outcome"]).where(Pod.id == pod_id))
    if not isinstance(raw, dict):
        return None
    try:
        return PodOutcome.model_validate(raw)
    except ValidationError:
        return None


__all__ = [
    "OutcomeChecklist",
    "OutcomeKind",
    "OutcomeMeasure",
    "PodOutcome",
    "read_pod_outcome",
]
