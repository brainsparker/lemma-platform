"""Carrying a channel line out of Lemma, one delivery at a time.

Channels plans a delivery for every send outside Lemma -- a line written in
Lemma for one of the channel's places, a push to a member who hears of it --
and announces it (``ChannelDeliveryDue``). This takes one: claims it, sends
it, and records what became of it on the same row -- with the platform's id
for the message, which is what the platform's later status report about it
names (``whatsapp_delivery_status``). A claimed delivery is never sent twice:
one that failed is due again after a backoff, until it is given up; one whose
sender died mid-send is settled without sending it again
(``channels.contracts.deliveries``).

Each send is bounded (``SEND_TIMEOUT``), below how long a claim is held, so a
slow platform is a failure tried again rather than a claim that lapses.

What goes to a place:

* **A run's answer** goes where its conversation answers, finishing a live
  stream in place where there is one (``run_answer_sends``).
* **Any other line** goes where it was written: a reply in a thread to the
  platform thread its root is in; a line at the top of a shared place to the
  place itself, not the thread of whoever spoke last (``place_threads``).

Every line to a contact's place -- a member's, an agent's answer, an
acknowledgement -- is first held to the contact's outreach rules, which
``contacts`` owns (``contacts.contracts.outreach``): opt-out first, then a
handle, then the platform's window (``contact_reach``), then, for a member's
line, the contact's day. Email to a contact carries a line, and
``List-Unsubscribe`` headers, saying how to stop. **A web chat** has nobody to
hand it to: it waits in their chat for their next visit.

What a member wrote to a contact goes into their conversation too, marked as a
member's (``append_follow_up``), so the agent answering them next knows a
person said it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from redis.asyncio import Redis

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.contact_conversations import append_follow_up
from app.modules.agent_surfaces.domain.adapter_port import IDEMPOTENCY_KEY
from app.modules.agent_surfaces.domain.deliveries import SEND_TIMEOUT
from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.delivery import track_sends
from app.modules.agent_surfaces.services.channel_lines import WEB
from app.modules.agent_surfaces.services.contact_reach import reach_for
from app.modules.agent_surfaces.services.delivery_outcome import (
    Outcome,
    Result,
    of_send,
)
from app.modules.agent_surfaces.services.cold_email_thread import (
    cold_thread_seed_id,
)
from app.modules.agent_surfaces.services.group_names import sender_name_for
from app.modules.agent_surfaces.services.member_pushes import (
    MemberPushes,
    Push,
    title_of,
)
from app.modules.agent_surfaces.services.notification_channels import can_cold_open
from app.modules.agent_surfaces.services.place_threads import (
    at_place_level,
    in_thread,
)
from app.modules.agent_surfaces.services.run_answer_sends import (
    EgressFor,
    RunAnswerSends,
    delivery_key,
    is_run_answer,
    send_errors,
)
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Delivery,
    Principal,
    SenderKind,
    Source,
    SourceKind,
    channel_by_id,
    member_of,
    source_by_id,
)
from app.modules.channels.contracts.deliveries import (
    claim_delivery,
    deliveries_of,
    delivery_dead,
    delivery_failed,
    delivery_interrupted,
    delivery_sent,
    delivery_skipped,
)
from app.modules.channels.contracts.lines import line_by_id
from app.modules.channels.contracts.places import addressed_at, conversation_at
from app.modules.contacts.contracts.outreach import (
    UNSUBSCRIBE_TOKEN_KEY,
    OutreachKind,
    Verdict,
    count_against_today,
    may_send,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Work:
    delivery: Delivery
    line: ChannelMessage
    channel: Channel


@dataclass(frozen=True, slots=True)
class Ready:
    """A line a contact's rules let through: its text, and what the send
    carries besides (an email's unsubscribe token)."""

    text: str
    metadata: dict[str, object] = field(default_factory=dict)


class ContactRefused(Exception):
    """A line a contact's rules do not let through; the reason is the member's."""

    def __init__(self, reason: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.retryable = retryable

    @classmethod
    def of(cls, verdict: Verdict) -> ContactRefused:
        return cls(
            verdict.reason or "Not sent.",
            code=str(verdict.code),
            retryable=verdict.retryable,
        )


def _outreach_kind(line: ChannelMessage) -> OutreachKind:
    if line.sender.kind is SenderKind.MEMBER:
        return OutreachKind.MEMBER_LINE
    return OutreachKind.AGENT_ANSWER


#: The pushes over one unit of work.
type PushesFor = Callable[[SqlAlchemyUnitOfWork], MemberPushes]


class ChannelDeliveries:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        egress_for: EgressFor,
        pushes_for: PushesFor,
        redis: Redis | None = None,
        send_timeout: float = SEND_TIMEOUT.total_seconds(),
    ) -> None:
        self.uow_factory = uow_factory
        self.egress_for = egress_for
        self.pushes_for = pushes_for
        #: Where a contact's day is counted; the shared client when ``None``.
        self.redis = redis
        self.send_timeout = send_timeout
        self.answers = RunAnswerSends(uow_factory, egress_for=egress_for)

    async def deliver(self, delivery_id: UUID) -> None:
        """Send one delivery, if it is due, and record the outcome on it."""
        work = await self._claim(delivery_id)
        if work is None:
            return
        try:
            async with asyncio.timeout(self.send_timeout):
                outcome = await self._outcome(work)
        except TimeoutError:
            logger.warning(
                "agent_surfaces.channel_deliveries.send_timed_out.degraded",
                delivery_id=str(delivery_id),
                timeout_seconds=self.send_timeout,
            )
            outcome = Outcome.failed(
                f"No answer from the platform within {self.send_timeout:g} s. "
                "It will be tried again."
            )
        await self._record(work.delivery, outcome)

    async def _outcome(self, work: _Work) -> Outcome:
        if work.line.is_deleted:
            return Outcome.skipped("The line was deleted before it went.")
        if work.delivery.source_id is not None:
            return await self._to_place(work, work.delivery.source_id)
        return await self._to_member(work)

    async def _claim(self, delivery_id: UUID) -> _Work | None:
        at = datetime.now(timezone.utc)
        async with self.uow_factory() as uow:
            delivery = await claim_delivery(uow, delivery_id, at=at)
            if delivery is None:
                await self._settle_interrupted(uow, delivery_id, at=at)
                await uow.commit()
                return None
            line = await line_by_id(uow, delivery.message_id)
            channel = await channel_by_id(uow, line.channel_id) if line else None
            if line is None or channel is None:
                await delivery_skipped(uow, delivery.id, reason="The line is gone.")
                await uow.commit()
                return None
            await uow.commit()
        return _Work(delivery=delivery, line=line, channel=channel)

    async def _settle_interrupted(
        self, uow: SqlAlchemyUnitOfWork, delivery_id: UUID, *, at: datetime
    ) -> None:
        """A claim whose sender died before recording what happened: settled,
        never sent again (see ``channels.infrastructure.deliveries``)."""
        settled = await delivery_interrupted(uow, delivery_id, at=at)
        if settled is not None:
            logger.warning(
                "agent_surfaces.channel_deliveries.send_interrupted.degraded",
                delivery_id=str(delivery_id),
                status=settled.status.value,
                platform=settled.platform,
            )

    # -- to a place -------------------------------------------------------------

    async def _to_place(self, work: _Work, source_id: UUID) -> Outcome:
        """The line, to the outside place it was written for, as the pod's bot."""
        line, channel = work.line, work.channel
        if line.members_only:
            return Outcome.skipped("A note for the pod's members goes nowhere else.")
        answer = is_run_answer(line)
        async with self.uow_factory() as uow:
            source = await source_by_id(uow, source_id)
            conversation_id = (
                line.conversation_id
                if answer
                else await conversation_at(uow, source.id)
                if source is not None
                else None
            )
        if source is None or source.channel_id != channel.id:
            return Outcome.skipped("That place is no longer this channel's.")
        platform = source.platform
        if platform != WEB and source.surface_id is None:
            # The bot that reached it was removed: trying again reaches nobody.
            return Outcome.skipped(
                "The bot that reached this place is no longer connected.",
                platform=platform,
            )
        try:
            ready = await self._ready(channel, source, line)
        except ContactRefused as refused:
            if refused.retryable:
                # The rules could not be read (Redis): try again, not give up.
                return Outcome.failed(refused.reason, platform=platform)
            return Outcome.skipped(refused.reason, platform=platform)
        if conversation_id is None:
            # Nobody there has spoken to the bot: only mail can write first.
            return await self._cold_open(work, source, ready)
        if platform == WEB:
            # Nobody to hand it to: it waits in their web chat for the next visit.
            await self._to_their_conversation(
                channel, conversation_id, line, ready.text, None
            )
            return Outcome.delivered(platform)
        if answer:
            return await self.answers.send(
                delivery=work.delivery,
                line=line,
                platform=platform,
                text=ready.text,
                metadata=ready.metadata,
            )
        receipt, outcome = await self._send(
            work.delivery,
            conversation_id,
            await self._aimed(line, source),
            text=ready.text,
            metadata=ready.metadata,
        )
        await self._to_their_conversation(
            channel, conversation_id, line, ready.text, receipt is not None
        )
        return outcome

    async def _ready(
        self, channel: Channel, source: Source, line: ChannelMessage
    ) -> Ready:
        """The text that goes, once a contact's rules allow it to; or a refusal
        (``ContactRefused``). A line counts against the contact's day once: a
        retry is the same line."""
        if channel.kind is not ChannelKind.CONTACT or channel.contact_id is None:
            return Ready(text=line.text)
        reach = reach_for(
            source.platform,
            address=source.external_user_id,
            last_inbound_at=source.last_inbound_at,
        )
        if reach is None:
            # A web chat: nobody is written to until they come back.
            return Ready(text=line.text)
        to, window = reach
        async with self.uow_factory() as uow:
            verdict = await may_send(
                uow,
                pod_id=channel.pod_id,
                contact_id=channel.contact_id,
                to=to,
                kind=_outreach_kind(line),
                at=datetime.now(timezone.utc),
                window=window,
            )
        if not verdict.allowed:
            raise ContactRefused.of(verdict)
        if line.sender.kind is SenderKind.MEMBER:
            counted = await count_against_today(
                contact_id=channel.contact_id, line_id=line.id, redis=self.redis
            )
            if not counted.allowed:
                raise ContactRefused.of(counted)
        metadata: dict[str, object] = {}
        if verdict.unsubscribe_token is not None:
            metadata[UNSUBSCRIBE_TOKEN_KEY] = verdict.unsubscribe_token
        return Ready(text=line.text + verdict.footer, metadata=metadata)

    async def _cold_open(self, work: _Work, source: Source, ready: Ready) -> Outcome:
        """A line to an email address nobody has written from yet -- a
        member's first word to a contact: a new thread, seeded so the reply
        comes back to this place (``cold_email_thread``)."""
        platform = source.platform
        nobody = Outcome.skipped(
            "The bot has not talked with anybody there yet.", platform=platform
        )
        if source.surface_id is None or not source.external_user_id:
            return nobody
        async with self.uow_factory() as uow:
            egress = self.egress_for(uow)
            surface = await egress.delivery.surface_repository.get(source.surface_id)
            if surface is None or not can_cold_open(surface):
                return nobody
            metadata = {
                **ready.metadata,
                IDEMPOTENCY_KEY: delivery_key(work.delivery),
                "email_sender_name": await sender_name_for(
                    uow, is_pod_default=True, agent_name=None, pod_id=surface.pod_id
                ),
            }
            try:
                thread = await egress.open_cold_email_thread(
                    surface=surface,
                    recipient_email=source.external_user_id,
                    subject=work.channel.title or title_of(work.line.text),
                    message=ready.text,
                    # By the line: a retry of it seeds the same thread.
                    thread_seed_id=cold_thread_seed_id(
                        notification_id=work.line.id, surface=surface
                    ),
                    metadata=metadata,
                )
            except send_errors() as exc:
                logger.warning(
                    "agent_surfaces.channel_deliveries.send_failed.degraded",
                    platform=platform,
                    error_type=type(exc).__name__,
                )
                thread = None
            if thread is None:
                return Outcome.failed(
                    f"{platform} did not take it. It will be tried again.",
                    platform=platform,
                )
            await addressed_at(uow, source.id, thread.last_event)
            await uow.commit()
        return Outcome.delivered(
            platform, external_message_id=thread.external_message_id
        )

    async def _aimed(self, line: ChannelMessage, source: Source) -> Source:
        """The place, aimed where in it the line was written."""
        ref = source.delivery_ref
        if ref is None:
            return source
        if line.thread_root_id is None:
            if source.kind is not SourceKind.SHARED:
                return source
            aimed = at_place_level(ref, source.platform)
        else:
            async with self.uow_factory() as uow:
                root = await line_by_id(uow, line.thread_root_id)
                sent_as = await self._sent_as(uow, root.id, source.id) if root else None
            if root is not None and root.source_id == source.id and root.reply_ref:
                # Heard there: its own event answers in its thread.
                aimed = dict(root.reply_ref)
            elif sent_as is not None:
                aimed = in_thread(ref, source.platform, root_message_id=sent_as)
            else:
                aimed = at_place_level(ref, source.platform)
        return source.model_copy(update={"delivery_ref": aimed})

    async def _sent_as(
        self, uow: SqlAlchemyUnitOfWork, line_id: UUID, source_id: UUID
    ) -> str | None:
        """The platform's id for a line written in Lemma and sent to a place."""
        for delivery in await deliveries_of(uow, [line_id]):
            if delivery.source_id == source_id and delivery.external_message_id:
                return delivery.external_message_id
        return None

    async def _send(
        self,
        delivery: Delivery,
        conversation_id: UUID,
        source: Source,
        *,
        text: str,
        metadata: dict[str, object],
    ) -> tuple[DeliveryReceipt | None, Outcome]:
        """Hand it to the platform at its place: its receipt, and the outcome."""
        receipt: DeliveryReceipt | None = None
        with track_sends() as sends:
            try:
                async with self.uow_factory() as uow:
                    receipt = await self.egress_for(uow).deliver_message(
                        conversation_id=conversation_id,
                        message=text,
                        metadata={**metadata, IDEMPOTENCY_KEY: delivery_key(delivery)},
                        at=source,
                    )
                    await uow.commit()
            except send_errors() as exc:
                logger.warning(
                    "agent_surfaces.channel_deliveries.send_failed.degraded",
                    platform=source.platform,
                    error_type=type(exc).__name__,
                )
        return receipt, of_send(receipt, sends, platform=source.platform)

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
            return Outcome.delivered(
                delivered.platform, external_message_id=delivered.external_message_id
            )
        if delivered.retryable:
            # A platform that was down is tried again; nowhere to reach them is not.
            return Outcome.failed(delivered.reason or "The push did not go.")
        # Nowhere to reach them is a fact about them, not an outage: the line
        # waits in Lemma, and trying again changes nothing.
        return Outcome.skipped(delivered.reason or "Nothing could reach them.")

    async def _record(self, delivery: Delivery, outcome: Outcome) -> None:
        reason = outcome.reason or "Not sent."
        async with self.uow_factory() as uow:
            match outcome.result:
                case Result.SENT:
                    await delivery_sent(
                        uow,
                        delivery.id,
                        platform=outcome.platform or "LEMMA",
                        external_message_id=outcome.external_message_id,
                    )
                case Result.SKIPPED:
                    await delivery_skipped(
                        uow, delivery.id, reason=reason, platform=outcome.platform
                    )
                case Result.DEAD:
                    await delivery_dead(
                        uow, delivery.id, reason=reason, platform=outcome.platform
                    )
                case Result.FAILED:
                    await delivery_failed(
                        uow, delivery, reason=reason, platform=outcome.platform
                    )
            await uow.commit()
        logger.info(
            "agent_surfaces.channel_deliveries.delivered.observed",
            platform=outcome.platform,
            result=outcome.result.value,
            has_platform_id=outcome.external_message_id is not None,
        )
