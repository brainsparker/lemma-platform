"""The Lemmas a person can start from."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.api.dependencies import CurrentUser
from app.modules.pod_bundle.infrastructure.outcome_templates import (
    OutcomeTemplateCard,
    shipped_cards,
)

router = APIRouter(prefix="/pods/bundle", tags=["Pod Bundle"], redirect_slashes=False)


@router.get(
    "/templates",
    response_model=list[OutcomeTemplateCard],
    status_code=status.HTTP_200_OK,
    operation_id="pod_bundle.templates",
    summary="Templates to start a Lemma from",
    description=(
        "The templates that ship with this server, one card each: what a Lemma "
        "made from it owns, its face, where its work happens and what it "
        "brings. Start one by creating a pod, importing the template with "
        "kind=TEMPLATE, and setting the card's outcome once it is applied."
    ),
)
async def list_templates(user: CurrentUser) -> list[OutcomeTemplateCard]:
    del user  # signed in is enough: the cards are the same for everyone
    return list(shipped_cards())
