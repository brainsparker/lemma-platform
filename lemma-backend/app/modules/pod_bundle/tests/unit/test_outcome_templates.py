"""Every shipped template is a card a person can start a Lemma from."""

from __future__ import annotations

from app.modules.pod_bundle.infrastructure.outcome_templates import DUE, shipped_cards
from app.modules.pod_bundle.infrastructure.templates import shipped_template_names


def test_every_shipped_template_is_on_the_shelf():
    cards = shipped_cards()
    assert {card.id for card in cards} == set(shipped_template_names())
    assert [card.position for card in cards] == sorted(card.position for card in cards)


def test_bars_hold_every_week_and_finish_lines_wait_for_a_date():
    for card in shipped_cards():
        assert card.kind in ("bar", "finish")
        assert (DUE in card.sentence) == (card.kind == "finish"), card.id
        assert 1 <= len(card.brings) <= 3, card.id
        assert card.face, card.id
