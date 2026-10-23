"""Where a pod stands against what it owns."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep, require_pod_membership
from app.modules.datastore.api.dependencies import RecordServiceDep, TableServiceDep
from app.modules.datastore.services.outcome_standing import outcome_standing
from app.modules.pod.contracts.outcome import PodOutcome

router = APIRouter(
    prefix="/pods/{pod_id}/outcome", tags=["query"], redirect_slashes=False
)


class WeekCountResponse(BaseModel):
    starts_on: date = Field(description="The Monday the week starts on (UTC).")
    met: int = Field(description="Rows that met the bar. For a count, every row.")
    total: int = Field(description="Rows the week holds.")


class StandingRowResponse(BaseModel):
    id: str
    title: str | None = None
    owner: str | None = None
    at: datetime | None = None


class BarStandingResponse(BaseModel):
    shape: str = Field(description="share or count")
    target: float
    label: str
    weeks: list[WeekCountResponse] = Field(
        description="Oldest first; the last is this week."
    )
    rows: list[StandingRowResponse] = Field(
        description="This week's rows behind the number: not met yet for a share, every row for a count."
    )
    rows_count: int


class FinishStepResponse(BaseModel):
    id: str
    title: str | None = None
    owner: str | None = None
    due_on: date | None = None
    done: bool
    behind: bool


class FinishStandingResponse(BaseModel):
    due_on: date
    since: date
    today: date
    days_left: int
    days_total: int
    total: int
    done: int
    behind: int
    steps: list[FinishStepResponse]


class OutcomeStandingResponse(BaseModel):
    outcome: PodOutcome | None = Field(
        description="What the pod owns, or null when nothing yet."
    )
    bar: BarStandingResponse | None = None
    finish: FinishStandingResponse | None = None
    problem: str | None = Field(
        default=None,
        description="Why it can't be counted, in plain words: a table or column it names is missing.",
    )


@router.get(
    "/standing",
    dependencies=[require_pod_membership("see where the pod stands")],
    response_model=OutcomeStandingResponse,
    status_code=status.HTTP_200_OK,
    operation_id="outcome.standing",
    summary="Where the pod stands",
    description=(
        "Where the pod stands against what it owns. A bar comes back as eight "
        "weeks of counts with this week's rows behind the number; a finish line "
        "as its checklist against the days left. Counted as the caller can see "
        "the rows: a table with row security counts only their own."
    ),
)
async def get_standing(
    pod_id: UUID,
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
    record_service: RecordServiceDep,
    table_service: TableServiceDep,
) -> OutcomeStandingResponse:
    standing = await outcome_standing(
        uow.session,
        pod_id=pod_id,
        user_id=user.id,
        ctx=ctx,
        record_service=record_service,
        table_service=table_service,
    )
    return OutcomeStandingResponse.model_validate(standing, from_attributes=True)
