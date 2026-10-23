"""The Lemmas a person can start from: one card per shipped template.

A template is a bundle under ``pod_bundle/templates/<name>/`` whose ``pod.json``
also says what a Lemma made from it owns (``outcome``) and how to show it as a
card (``lemma``). The card is read from those files, never written twice: the
sentence, the face, the place it reads from and the three things it brings.

A finish line's sentence carries ``{due}``, filled with the date the person
picks; its outcome is checked here with a stand-in date, so a template whose
outcome could never be set is left off the shelf rather than failing later.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.modules.pod.contracts.outcome import (
    OutcomeChecklist,
    OutcomeMeasure,
    PodOutcome,
)
from app.modules.pod_bundle.infrastructure.templates import (
    TEMPLATES_ROOT,
    shipped_template_names,
    template_root,
)

DUE = "{due}"


class TemplateOutcome(BaseModel):
    """The outcome a template's Lemma owns, before its date is known: a finish
    line's sentence still carries ``{due}``."""

    sentence: str
    kind: str
    measure: OutcomeMeasure | None = None
    checklist: OutcomeChecklist | None = None


class OutcomeTemplateCard(BaseModel):
    """One Lemma a person can start, as the shelf shows it."""

    id: str = Field(
        description="The template's name, passed to an import as kind=TEMPLATE."
    )
    name: str = Field(description="The short name a Lemma made from it starts with.")
    description: str | None = None
    sentence: str = Field(description="What it owns. A finish line's carries {due}.")
    kind: str = Field(description="bar or finish")
    face: str | None = Field(
        default=None, description="Which character's face it wears."
    )
    place: str | None = Field(
        default=None, description="Where its work usually happens."
    )
    brings: list[str] = Field(default_factory=list)
    outcome: TemplateOutcome = Field(
        description="The outcome to set once imported, with {due} filled in."
    )
    position: int = 100


def read_card(name: str, *, root: Path = TEMPLATES_ROOT) -> OutcomeTemplateCard | None:
    folder = template_root(name, root=root)
    if folder is None:
        return None
    try:
        manifest = json.loads((folder / "pod.json").read_text())
    except OSError, ValueError:
        return None
    outcome = manifest.get("outcome")
    lemma = manifest.get("lemma") or {}
    if not isinstance(outcome, dict) or not isinstance(lemma, dict):
        return None
    sentence = str(outcome.get("sentence") or "")
    trial = {**outcome, "sentence": sentence.replace(DUE, "December 1")}
    if outcome.get("kind") == "finish":
        trial["due_on"] = date(2099, 12, 1).isoformat()
    try:
        PodOutcome.model_validate(trial)
    except ValidationError:
        return None
    return OutcomeTemplateCard(
        id=name,
        name=str(manifest.get("name") or name),
        description=manifest.get("description"),
        sentence=sentence,
        kind=str(outcome.get("kind")),
        face=lemma.get("face"),
        place=lemma.get("place"),
        brings=[str(item) for item in lemma.get("brings") or []][:3],
        outcome=TemplateOutcome.model_validate(outcome),
        position=int(lemma.get("position") or 100),
    )


@lru_cache(maxsize=1)
def shipped_cards() -> tuple[OutcomeTemplateCard, ...]:
    """Every card this server ships, in shelf order. Read once: the files
    ship with the server and do not change while it runs."""
    cards = [
        card for card in (read_card(name) for name in shipped_template_names()) if card
    ]
    return tuple(sorted(cards, key=lambda card: (card.position, card.name)))
