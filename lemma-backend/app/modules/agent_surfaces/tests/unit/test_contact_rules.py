"""What a member may send a contact, channel by channel, and how it goes.

The rules (``contact_rules``) are pinned directly; the delivery of a member's
line to a contact's place (``member_posts.deliver_member_line``) is followed
over the channels, contacts and platform contracts it calls, doubled where
they are published.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import unquote
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.services import member_posts
from app.modules.agent_surfaces.services.contact_rules import (
    ContactRefused,
    handle_for_unsubscribe,
    handle_on,
    unsubscribe_line,
    unsubscribe_token,
    within_the_days_lines,
)
from app.modules.agent_surfaces.services.contact_windows import Window
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Sender,
    Source,
    SourceKind,
)
from app.modules.channels.contracts.deliveries import DeliveryStatus
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


# ------------------------------------------------- a member's line, delivered


class _Post:
    """One member's line in a contact's channel, and what became of it."""

    def __init__(self, monkeypatch, *, platform: str, handles, members_only=False):
        self.pod_id, self.contact_id, self.member = uuid4(), uuid4(), uuid4()
        self.conversation_id = uuid4()
        self.channel = Channel(
            id=uuid4(),
            pod_id=self.pod_id,
            kind=ChannelKind.CONTACT,
            contact_id=self.contact_id,
            created_at=NOW,
        )
        self.source = Source(
            id=uuid4(),
            channel_id=self.channel.id,
            pod_id=self.pod_id,
            surface_id=uuid4(),
            platform=platform,
            kind=SourceKind.PRIVATE,
        )
        self.line = ChannelMessage(
            id=uuid4(),
            channel_id=self.channel.id,
            pod_id=self.pod_id,
            sender=Sender.member(self.member),
            text="Your order ships Friday.",
            source_id=self.source.id,
            members_only=members_only,
            created_at=NOW,
        )
        self.platform_takes = True
        self.sent: list[dict] = []
        self.heard: list[dict] = []
        self.deliveries: list[dict] = []
        self.windows = SimpleNamespace(follow_up=AsyncMock(return_value=Window.OPEN))

        async def egress_send(**kwargs):
            self.sent.append(kwargs)
            return self.platform_takes

        async def record(_uow, **delivery):
            self.deliveries.append(delivery)

        async def heard(_uow, **kwargs):
            self.heard.append(kwargs)

        doubles = {
            "line_by_id": AsyncMock(return_value=self.line),
            "channel_by_id": AsyncMock(return_value=self.channel),
            "source_by_id": AsyncMock(return_value=self.source),
            "conversation_at": AsyncMock(return_value=self.conversation_id),
            "contact_handles": AsyncMock(return_value=list(handles)),
            "build_surface_egress": lambda _uow: SimpleNamespace(
                send_agent_message_for_conversation=egress_send
            ),
            "append_follow_up": heard,
            "record_delivery": record,
        }
        for name, double in doubles.items():
            monkeypatch.setattr(member_posts, name, double)

    @asynccontextmanager
    async def _uow(self):
        yield SimpleNamespace(commit=AsyncMock())

    async def deliver(self) -> None:
        await member_posts.deliver_member_line(
            self._uow, message_id=self.line.id, windows=self.windows
        )

    @property
    def outcome(self) -> tuple[DeliveryStatus, str | None]:
        [delivery] = self.deliveries
        return delivery["status"], delivery["error"]


async def test_an_email_to_a_contact_says_how_to_stop_them(monkeypatch):
    email = _handle(IdentityKind.EMAIL, wrote=NOW)
    post = _Post(monkeypatch, platform="RESEND", handles=[email])

    await post.deliver()

    [sent] = post.sent
    assert sent["message"].startswith("Your order ships Friday.")
    assert "To stop these emails:" in sent["message"]
    assert sent["at"] is post.source
    assert post.outcome == (DeliveryStatus.SENT, None)
    # Their conversation hears it as a member's, so the agent knows who said it.
    [heard] = post.heard
    assert (heard["sent_by_user_id"], heard["delivered"]) == (post.member, True)


async def test_a_refused_line_is_recorded_with_the_members_reason(monkeypatch):
    """WhatsApp past the window: nothing goes, and the line says why."""
    stale = _handle(IdentityKind.PHONE, wrote=NOW - timedelta(days=2))
    post = _Post(monkeypatch, platform="WHATSAPP", handles=[stale])

    await post.deliver()

    assert post.sent == []
    status, reason = post.outcome
    assert status is DeliveryStatus.SKIPPED
    assert "24 hours" in (reason or "")


async def test_a_contact_at_their_days_cap_is_not_written_to(monkeypatch):
    post = _Post(
        monkeypatch, platform="TELEGRAM", handles=[_handle(IdentityKind.TELEGRAM)]
    )
    post.windows.follow_up.return_value = Window.SHUT

    await post.deliver()

    assert post.sent == []
    assert post.outcome[0] is DeliveryStatus.SKIPPED


async def test_a_web_chat_line_waits_for_their_next_visit(monkeypatch):
    """Nobody to hand it to: it is in their chat, and counts as there."""
    post = _Post(monkeypatch, platform="WEB", handles=[])

    await post.deliver()

    assert post.sent == []
    [heard] = post.heard
    assert heard["delivered"] is None
    assert post.outcome == (DeliveryStatus.SENT, None)


async def test_a_line_the_platform_did_not_take_is_failed_not_sent(monkeypatch):
    post = _Post(
        monkeypatch, platform="TELEGRAM", handles=[_handle(IdentityKind.TELEGRAM)]
    )
    post.platform_takes = False

    await post.deliver()

    status, reason = post.outcome
    assert status is DeliveryStatus.FAILED
    assert "TELEGRAM did not take it" in (reason or "")
    assert post.heard[0]["delivered"] is False


async def test_a_members_only_note_never_leaves_lemma(monkeypatch):
    post = _Post(
        monkeypatch,
        platform="TELEGRAM",
        handles=[_handle(IdentityKind.TELEGRAM)],
        members_only=True,
    )

    await post.deliver()

    assert (post.sent, post.deliveries, post.heard) == ([], [], [])
