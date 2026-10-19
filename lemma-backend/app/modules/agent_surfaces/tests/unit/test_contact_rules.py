"""What a member may send a contact, channel by channel.

The rules (``contact_rules``) are pinned directly. How a line to a contact's
place goes -- the delivery row it becomes, and what the rules make of it -- is
followed against the database in ``e2e/test_channel_deliveries_e2e.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import unquote
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.services.contact_rules import (
    ContactRefused,
    handle_for_unsubscribe,
    handle_on,
    unsubscribe_line,
    unsubscribe_token,
    within_the_days_lines,
)
from app.modules.agent_surfaces.services.contact_windows import Window
from app.modules.contacts.contracts import (
    ContactHandle,
    IdentityKind,
    IdentityStrength,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def _handle(
    kind: IdentityKind,
    *,
    value: str = "x",
    wrote=None,
    unsubscribed=None,
    strength=IdentityStrength.CHANNEL,
    verified_at=NOW,
) -> ContactHandle:
    return ContactHandle(
        id=uuid4(),
        contact_id=uuid4(),
        kind=kind,
        value=value,
        strength=strength,
        verified_at=verified_at,
        last_inbound_at=wrote,
        unsubscribed_at=unsubscribed,
    )


# ------------------------------------------------------------------ the rules


def test_whatsapp_is_open_for_a_day_after_the_contact_last_wrote():
    recent = _handle(IdentityKind.PHONE, wrote=NOW - timedelta(hours=23))

    assert handle_on("WHATSAPP", [recent], NOW) == recent
    with pytest.raises(ContactRefused) as refused:
        handle_on(
            "WHATSAPP",
            [_handle(IdentityKind.PHONE, wrote=NOW - timedelta(hours=25))],
            NOW,
        )
    assert refused.value.code == "outside_window"


def test_the_window_runs_from_their_latest_message_on_any_of_their_numbers():
    """Two numbers, one fresh: the fresh one is where it goes."""
    stale = _handle(IdentityKind.PHONE, wrote=NOW - timedelta(days=3))
    fresh = _handle(IdentityKind.PHONE, wrote=NOW - timedelta(hours=1))

    assert handle_on("WHATSAPP", [stale, fresh], NOW) == fresh


def test_nobody_is_written_to_where_they_unsubscribed():
    with pytest.raises(ContactRefused) as refused:
        handle_on(
            "RESEND", [_handle(IdentityKind.EMAIL, wrote=NOW, unsubscribed=NOW)], NOW
        )
    assert refused.value.code == "unsubscribed"


def test_one_unsubscribed_address_silences_only_that_address():
    """Unsubscribed at an old work address, still reading a personal one."""
    work = _handle(
        IdentityKind.EMAIL, value="dana@old.example", wrote=NOW, unsubscribed=NOW
    )
    personal = _handle(IdentityKind.EMAIL, value="dana@home.example", wrote=NOW)

    assert handle_on("RESEND", [work, personal], NOW) == personal
    assert handle_on("RESEND", [work, personal], NOW, at="Dana@Home.example") == (
        personal
    )
    # A place that writes to the address they left is still refused.
    with pytest.raises(ContactRefused) as refused:
        handle_on("RESEND", [work, personal], NOW, at="dana@old.example")
    assert refused.value.code == "unsubscribed"


def test_a_place_names_its_number_however_it_is_spelled():
    old = _handle(IdentityKind.PHONE, value="+44 7700 900001", wrote=NOW)
    new = _handle(IdentityKind.PHONE, value="+447700900002", wrote=NOW)

    assert handle_on("WHATSAPP", [old, new], NOW, at="447700900001") == old


def test_a_channel_the_contact_has_no_handle_on_is_refused():
    with pytest.raises(ContactRefused) as refused:
        handle_on("TELEGRAM", [_handle(IdentityKind.EMAIL, wrote=NOW)], NOW)
    assert refused.value.code == "no_handle"


def test_a_web_chat_needs_no_handle():
    assert handle_on("WEB", [], NOW) is None


def test_an_unsubscribe_link_names_one_handle_and_cannot_be_altered():
    handle = uuid4()
    token = unsubscribe_token(handle)

    assert handle_for_unsubscribe(token) == handle
    assert handle_for_unsubscribe(f"{uuid4()}.{token.split('.', 1)[1]}") is None
    assert handle_for_unsubscribe("junk") is None


def test_the_unsubscribe_line_links_to_that_handle():
    handle = _handle(IdentityKind.EMAIL)

    line = unsubscribe_line(handle)

    assert "To stop these emails:" in line
    assert "/public/contacts/unsubscribe?token=" in line
    token = unquote(line.rsplit("token=", 1)[1])
    assert handle_for_unsubscribe(token) == handle.id


@pytest.mark.parametrize(
    ("window", "code"), [(Window.SHUT, "rate_limited"), (Window.UNKNOWN, "unavailable")]
)
async def test_a_contacts_day_is_capped_and_an_unknown_count_is_shut(window, code):
    """Fails closed: a business that cannot count what it sent does not send."""
    windows = SimpleNamespace(follow_up=AsyncMock(return_value=window))

    with pytest.raises(ContactRefused) as refused:
        await within_the_days_lines(windows, uuid4())

    assert refused.value.code == code


async def test_a_contacts_day_with_room_lets_the_line_through():
    windows = SimpleNamespace(follow_up=AsyncMock(return_value=Window.OPEN))

    assert await within_the_days_lines(windows, uuid4()) is None
