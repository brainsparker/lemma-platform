"""The cutover's converters for the identity events the old transport still owed.

Each writes what the producing code writes today when the event happens
(``events/identity_work``): the email for a sign-up, a replaced phone, an
invitation or an accepted one, keyed by the event's id; the verification of
a received WhatsApp code, keyed by its message; and the ``UserMobileChanged``
fact a changed number raises, whose one subscriber unbinds what was bound to
the old number. Worker only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.domain.events import DomainEvent
from app.core.infrastructure.cutover.routes import legacy_converter
from app.core.infrastructure.work import JobContext
from app.modules.identity.domain.events import (
    OrganizationInvitationAcceptedEvent,
    OrganizationInvitationCreatedEvent,
    UserPhoneReplacedEvent,
    UserSignedUpEvent,
)
from app.modules.identity.events.identity_work import (
    WhatsAppVerificationPayload,
    add_whatsapp_verification_work,
    emit_mobile_changed,
    stage_identity_work,
)

_EMAIL_EVENTS: dict[str, type[DomainEvent]] = {
    cls.get_event_type(): cls
    for cls in (
        UserSignedUpEvent,
        UserPhoneReplacedEvent,
        OrganizationInvitationCreatedEvent,
        OrganizationInvitationAcceptedEvent,
    )
}


@legacy_converter("identity-email-events")
async def identity_email(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    del route
    cls = _EMAIL_EVENTS.get(str(event.get("event_type")))
    if cls is None:
        return
    raised = cls.model_validate(event)
    async with ctx.complete_work() as uow:
        await stage_identity_work(uow, [raised])


@legacy_converter("identity-mobile-verification-events")
async def whatsapp_verification(
    ctx: JobContext, event: dict[str, Any], route: str
) -> None:
    del route
    payload = WhatsAppVerificationPayload.model_validate(event)
    async with ctx.complete_work() as uow:
        await add_whatsapp_verification_work(uow, payload)


@legacy_converter("agent-surfaces.identity")
async def mobile_changed(ctx: JobContext, event: dict[str, Any], route: str) -> None:
    del route
    async with ctx.complete_work() as uow:
        await emit_mobile_changed(uow, UUID(str(event["user_id"])))
