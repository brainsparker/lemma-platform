"""What a pod owns: set it, change it, or let it go."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from app.core.authorization.dependencies import PodContextDep
from app.modules.pod.api.dependencies import PodEditorDep, PodServiceDep
from app.modules.pod.domain.pod_entities import PodOutcome

router = APIRouter(
    prefix="/pods/{pod_id}/outcome", tags=["Pods"], redirect_slashes=False
)


@router.put(
    "",
    dependencies=[PodEditorDep],
    response_model=PodOutcome,
    status_code=status.HTTP_200_OK,
    operation_id="outcome.set",
    summary="Set what the pod owns",
    description=(
        "Set the one outcome the pod owns: a bar held every week, counted from "
        "rows of one of its tables, or a finish line by a date, counted from a "
        "checklist. Replaces any outcome it had. A finish line with no `since` "
        "counts its days from today."
    ),
)
async def set_outcome(
    pod_id: UUID,
    data: PodOutcome,
    pod_service: PodServiceDep,
    ctx: PodContextDep,
) -> PodOutcome:
    pod = await pod_service.set_outcome(pod_id, data, ctx)
    assert pod.config.outcome is not None
    return pod.config.outcome


@router.delete(
    "",
    dependencies=[PodEditorDep],
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="outcome.clear",
    summary="Clear what the pod owns",
    description="Let the pod's outcome go. Nothing else about the pod changes.",
)
async def clear_outcome(
    pod_id: UUID,
    pod_service: PodServiceDep,
    ctx: PodContextDep,
) -> Response:
    await pod_service.set_outcome(pod_id, None, ctx)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
