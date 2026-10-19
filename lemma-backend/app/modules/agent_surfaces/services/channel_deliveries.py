"""Carrying a channel line out of Lemma, one delivery at a time.

Channels plans a delivery for every send outside Lemma -- a line written in
Lemma for one of the channel's places, a push to a member who hears of it --
and announces it (``ChannelDeliveryDue``). This takes one: claims it, sends
it, and records what became of it on the same row. A claimed delivery that
was sent is never sent again; one that failed is due again after a backoff,
until it is given up (``channels.contracts.deliveries``).

Writing to a contact keeps the rules a business is held to, and a contact
asked for:

* **Never a handle they unsubscribed**, and email always carries a line, and a
  link, saying how to stop.
* **WhatsApp only within 24 hours** of their last message: past it Meta takes
  only pre-approved templates, which are not built here.
* **At most ``surface_contact_follow_ups_per_contact_per_day``** lines a day.
* **A web chat** has nobody to hand it to: it waits in their chat for their
  next visit.

What a member wrote to a contact goes into their conversation too, marked as a
member's (``append_follow_up``), so the agent answering them next knows a
person said it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.contact_conversations import append_follow_up
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.services.channel_lines import ALREADY_A_LINE, WEB
from app.modules.agent_surfaces.services.contact_rules import (
    ContactRefused,
    handle_on,
    unsubscribe_line,
    within_the_days_lines,
)
from app.modules.agent_surfaces.services.contact_windows import ContactWindows
from app.modules.agent_surfaces.services.member_pushes import MemberPushes, Push
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Delivery,
    Principal,
    SenderKind,
    Source,
    channel_by_id,
    member_of,
    source_by_id,
)
from app.modules.channels.contracts.deliveries import (
    claim_delivery,
    delivery_failed,
    delivery_sent,
    delivery_skipped,
)
from app.modules.channels.contracts.lines import line_by_id
from app.modules.channels.contracts.places import conversation_at
from app.modules.contacts.contracts import IdentityKind, contact_handles

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Outcome:
    """What became of one send: sent, skipped on purpose, or failed."""

    sent: bool = False
    platform: str | None = None
    reason: str | None = None
    #: Not worth trying again: a refusal, not an outage.
    final: bool = False

    @classmethod
    def skipped(cls, reason: str, *, platform: str | None = None) -> Outcome:
        return cls(reason=reason, platform=platform, final=True)

    @classmethod
    def failed(cls, reason: str, *, platform: str | None = None) -> Outcome:
        return cls(reason=reason, platform=platform)


@dataclass(frozen=True, slots=True)
class _Work:
    delivery: Delivery
    line: ChannelMessage
    channel: Channel


class PlaceEgress(Protocol):
    """What sends a line to a place: ``SurfaceEgress``."""

    async def send_agent_message_for_conversation(
        self,
        *,
        conversation_id: UUID,
        message: str,
        metadata: dict[str, object] | None = None,
        at: Source | None = None,
    ) -> bool: ...


#: The egress over one unit of work: ``build_surface_egress``, or a stand-in.
type EgressFor = Callable[[SqlAlchemyUnitOfWork], PlaceEgress]
#: The pushes over one unit of work.
type PushesFor = Callable[[SqlAlchemyUnitOfWork], MemberPushes]


class ChannelDeliveries:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        egress_for: EgressFor,
        pushes_for: PushesFor,
        windows: ContactWindows | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.egress_for = egress_for
        self.pushes_for = pushes_for
        self.windows = windows

    async def deliver(self, delivery_id: UUID) -> None:
        """Send one delivery, if it is due, and record the outcome on it."""
        work = await self._claim(delivery_id)
        if work is None:
            return
        line = work.line
        if line.is_deleted:
            outcome = Outcome.skipped("The line was deleted before it went.")
        elif work.delivery.source_id is not None:
            outcome = await self._to_place(work, work.delivery.source_id)
        else:
            outcome = await self._to_member(work)
        await self._record(work.delivery, outcome)

    async def _claim(self, delivery_id: UUID) -> _Work | None:
        async with self.uow_factory() as uow:
            delivery = await claim_delivery(
                uow, delivery_id, at=datetime.now(timezone.utc)
            )
            if delivery is None:
                return None
            line = await line_by_id(uow, delivery.message_id)
            channel = await channel_by_id(uow, line.channel_id) if line else None
            if line is None or channel is None:
                await delivery_skipped(uow, delivery.id, reason="The line is gone.")
                await uow.commit()
                return None
            await uow.commit()
        return _Work(delivery=delivery, line=line, channel=channel)

    # -- to a place -------------------------------------------------------------

    async def _to_place(self, work: _Work, source_id: UUID) -> Outcome:
        """The line, to the outside place it was written for, as the pod's bot."""
        line, channel = work.line, work.channel
        if line.members_only:
            return Outcome.skipped("A note for the pod's members goes nowhere else.")
        async with self.uow_factory() as uow:
            source = await source_by_id(uow, source_id)
            conversation_id = (
                await conversation_at(uow, source.id) if source is not None else None
            )
        if source is None or source.channel_id != channel.id:
            return Outcome.skipped("That place is no longer this channel's.")
        platform = source.platform
        try:
            text = await self._ready(
                channel, source, line, first_try=work.delivery.attempts <= 1
            )
        except ContactRefused as refused:
            if refused.code == "unavailable":
                # The rules could not be read (Redis): try again, not give up.
                return Outcome.failed(refused.message, platform=platform)
            return Outcome.skipped(refused.message, platform=platform)
        if conversation_id is None:
            return Outcome.skipped(
                "The bot has not talked with anybody there yet.", platform=platform
            )
        if platform == WEB:
            # Nobody to hand it to: it waits in their web chat for the next visit.
            await self._to_their_conversation(
                channel, conversation_id, line, text, None
            )
            return Outcome(sent=True, platform=platform)
        sent = await self._send(conversation_id, source, line, text)
        await self._to_their_conversation(channel, conversation_id, line, text, sent)
        if sent:
            return Outcome(sent=True, platform=platform)
        return Outcome.failed(
            f"{platform} did not take it. It will be tried again.", platform=platform
        )

    async def _ready(
        self,
        channel: Channel,
        source: Source,
        line: ChannelMessage,
        *,
        first_try: bool,
    ) -> str:
        """The text that goes, once a contact's rules allow it to; or a refusal.
        A line counts against the contact's day once: a retry is the same line."""
        if channel.kind is not ChannelKind.CONTACT or channel.contact_id is None:
            return line.text
        async with self.uow_factory() as uow:
            handles = await contact_handles(
                uow, pod_id=channel.pod_id, contact_id=channel.contact_id
            )
        handle = handle_on(
            source.platform,
            handles,
            datetime.now(timezone.utc),
            at=source.external_user_id,
        )
        if line.sender.kind is SenderKind.MEMBER and first_try:
            await within_the_days_lines(
                self.windows or ContactWindows(), channel.contact_id
            )
        if handle is not None and handle.kind is IdentityKind.EMAIL:
            return line.text + unsubscribe_line(handle)
        return line.text

    async def _send(
        self, conversation_id: UUID, source: Source, line: ChannelMessage, text: str
    ) -> bool:
        """Hand it to the platform at its place; whether the platform took it."""
        try:
            async with self.uow_factory() as uow:
                egress = self.egress_for(uow)
                sent = await egress.send_agent_message_for_conversation(
                    conversation_id=conversation_id,
                    message=text,
                    # The timeline holds it already: the send writes no second line.
                    metadata={ALREADY_A_LINE: str(line.id)},
                    at=source,
                )
                await uow.commit()
        except platform_transport_errors() as exc:
            logger.warning(
                "agent_surfaces.channel_deliveries.send_failed.degraded",
                platform=source.platform,
                error_type=type(exc).__name__,
            )
            return False
        return bool(sent)

    async def _to_their_conversation(
        self,
        channel: Channel,
        conversation_id: UUID,
        line: ChannelMessage,
        text: str,
        delivered: bool | None,
    ) -> None:
        """A contact's conversation hears what a member wrote them, as theirs."""
        if channel.kind is not ChannelKind.CONTACT or line.sender.user_id is None:
            return
        async with self.uow_factory() as uow:
            await append_follow_up(
                uow,
                conversation_id=conversation_id,
                message=text,
                sent_by_user_id=line.sender.user_id,
                delivered=delivered,
            )
            await uow.commit()

    # -- to a member ------------------------------------------------------------

    async def _to_member(self, work: _Work) -> Outcome:
        recipient = work.delivery.recipient_user_id
        if recipient is None:  # pragma: no cover - a CHECK holds it to one target
            return Outcome.skipped("This delivery names nobody.")
        async with self.uow_factory() as uow:
            member = await member_of(
                uow, channel_id=work.channel.id, principal=Principal.member(recipient)
            )
            try:
                delivered = await self.pushes_for(uow).push(
                    Push(
                        channel=work.channel,
                        line=work.line,
                        recipient_user_id=recipient,
                        member=member,
                    )
                )
            except platform_transport_errors() as exc:
                return Outcome.failed(f"The push did not go: {type(exc).__name__}.")
            await uow.commit()
        if delivered.sent:
            return Outcome(sent=True, platform=delivered.platform)
        if delivered.retryable:
            # A platform that was down is tried again; nowhere to reach them is not.
            return Outcome.failed(delivered.reason or "The push did not go.")
        # Nowhere to reach them is a fact about them, not an outage: the line
        # waits in Lemma, and trying again changes nothing.
        return Outcome.skipped(delivered.reason or "Nothing could reach them.")

    async def _record(self, delivery: Delivery, outcome: Outcome) -> None:
        async with self.uow_factory() as uow:
            if outcome.sent:
                await delivery_sent(
                    uow,
                    delivery.id,
                    platform=outcome.platform or "LEMMA",
                    external_message_id=None,
                )
            elif outcome.final:
                await delivery_skipped(
                    uow,
                    delivery.id,
                    reason=outcome.reason or "Not sent.",
                    platform=outcome.platform,
                )
            else:
                await delivery_failed(
                    uow,
                    delivery,
                    reason=outcome.reason or "Not sent.",
                    platform=outcome.platform,
                )
            await uow.commit()
        logger.info(
            "agent_surfaces.channel_deliveries.delivered.observed",
            platform=outcome.platform,
            sent=outcome.sent,
            final=outcome.final,
        )
