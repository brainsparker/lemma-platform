"""What a business may send a contact, and how they stop it.

The rules every line a member writes to a contact is held to
(``services.member_posts``):

* **Never a handle they unsubscribed**, and every email carries a line, and a
  signed link, saying how to stop (``unsubscribe_token``).
* **WhatsApp only within 24 hours** of their last message: past it Meta takes
  only pre-approved templates, which are not built here.
* **Telegram** any time -- a bot only ever writes to somebody who wrote first.
* **At most ``surface_contact_follow_ups_per_contact_per_day``** a day.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import UUID

from app.core.config import settings
from app.core.crypto.factory import get_secret_signer
from app.core.domain.errors import DomainError
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.services.contact_windows import (
    ContactWindows,
    Window,
)
from app.modules.contacts.contracts import ContactHandle, IdentityKind

WHATSAPP_WINDOW = timedelta(hours=24)

#: The signing purpose of an unsubscribe link: a link for one handle, forever.
UNSUBSCRIBE_PURPOSE = "contact-unsubscribe"

_KIND_FOR_PLATFORM = {
    SurfacePlatform.WHATSAPP.value: IdentityKind.PHONE,
    SurfacePlatform.TELEGRAM.value: IdentityKind.TELEGRAM,
    SurfacePlatform.RESEND.value: IdentityKind.EMAIL,
}


class ContactRefused(DomainError):
    """A line a contact's rules do not let through; the reason is the member's."""

    def __init__(self, message: str, *, code: str, status_code: int = 409) -> None:
        super().__init__(message, code=code, status_code=status_code)


def unsubscribe_token(handle_id: UUID) -> str:
    """A link token that unsubscribes exactly one handle, and can't be forged."""
    payload = str(handle_id)
    return (
        f"{payload}.{get_secret_signer().sign(UNSUBSCRIBE_PURPOSE, payload.encode())}"
    )


def handle_for_unsubscribe(token: str) -> UUID | None:
    payload, _, signature = token.partition(".")
    if not signature or not get_secret_signer().verify(
        UNSUBSCRIBE_PURPOSE, payload.encode(), signature
    ):
        return None
    try:
        return UUID(payload)
    except ValueError:
        return None


def unsubscribe_line(handle: ContactHandle) -> str:
    base = str(settings.api_url).rstrip("/")
    link = f"{base}/public/contacts/unsubscribe?token={quote(unsubscribe_token(handle.id))}"
    return f"\n\n--\nTo stop these emails: {link}"


def handle_on(
    platform: str, handles: list[ContactHandle], now: datetime
) -> ContactHandle | None:
    """The handle a follow-up goes to on this platform, or a refusal."""
    kind = _KIND_FOR_PLATFORM.get(platform)
    if kind is None:
        return None
    mine = [handle for handle in handles if handle.kind is kind]
    if not mine:
        raise ContactRefused(
            "The contact has no handle on that channel", code="no_handle"
        )
    if any(handle.unsubscribed_at is not None for handle in mine):
        raise ContactRefused(
            "The contact asked not to be written to there", code="unsubscribed"
        )
    latest = max(
        mine,
        key=lambda handle: (
            handle.last_inbound_at or datetime.min.replace(tzinfo=timezone.utc)
        ),
    )
    if platform == SurfacePlatform.WHATSAPP.value and (
        latest.last_inbound_at is None or now - latest.last_inbound_at > WHATSAPP_WINDOW
    ):
        raise ContactRefused(
            "WhatsApp lets a business write freely only within 24 hours of the "
            "contact's last message, and approved templates are not available",
            code="outside_window",
        )
    return latest


async def within_the_days_lines(windows: ContactWindows, contact_id: UUID) -> None:
    window = await windows.follow_up(contact_id)
    if window is Window.SHUT:
        raise ContactRefused(
            "That is as many messages as one contact gets in a day",
            code="rate_limited",
            status_code=429,
        )
    if window is Window.UNKNOWN:
        raise ContactRefused(
            "Messages to contacts are unavailable right now. Try again shortly.",
            code="unavailable",
            status_code=503,
        )
