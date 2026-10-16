"""The contacts cap an organization is held to, and whether it is spent.

Three states, and the difference between the last two is the point: an
organization that never chose gets the deployment's default, so a bot anybody
can write to always has a ceiling; an owner who removed the cap chose no limit,
and the default must not quietly put one back.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.modules.usage.domain.accounting import money


@dataclass(frozen=True, slots=True)
class StoredContactsCap:
    """What an owner set: a monthly limit and a number of contacts, ``None``
    for no limit on either."""

    monthly_limit_usd: Decimal | None
    max_contacts: int | None = None


@dataclass(frozen=True, slots=True)
class ContactsCap:
    """The cap that applies this month."""

    limit_usd: Decimal | None
    #: Nobody in the organization has set one; this is the deployment's.
    is_default: bool
    #: How many contacts its bots may answer at all; ``None`` for no limit.
    max_contacts: int | None = None


def effective_contacts_cap(
    stored: StoredContactsCap | None,
    *,
    default_usd: float | None,
    default_max_contacts: int | None = None,
) -> ContactsCap:
    if stored is not None:
        return ContactsCap(
            limit_usd=stored.monthly_limit_usd,
            is_default=False,
            max_contacts=stored.max_contacts,
        )
    return ContactsCap(
        limit_usd=None if default_usd is None else money(default_usd),
        is_default=True,
        max_contacts=default_max_contacts,
    )


def may_answer_another_contact(cap: ContactsCap, *, answered: int) -> bool:
    """Whether a contact nobody has answered yet may be: the count has room."""
    return cap.max_contacts is None or answered < cap.max_contacts


def cap_reached(cap: ContactsCap, *, consumed_usd: Decimal) -> bool:
    """Whether what was spent and is still reserved has reached the cap."""
    return cap.limit_usd is not None and consumed_usd >= cap.limit_usd
