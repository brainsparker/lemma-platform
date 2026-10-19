"""Carrying a line to a member where they are: their chat app, or email.

A push is one delivery of a line to one member (``channels`` decided they
hear of it). Where it goes is the member's choice when they made one
(``notify_via``, in order), else the sending agent's best way to reach them:
the chat app they last wrote to it on, then email
(``notification_channels``). The first channel that takes it wins -- three
copies across three apps is how a useful feature comes to read as spam.

A push stands for every line of one channel the member heard since their
last push from it (``channels`` coalesces them): it carries the newest -- or
the one that names or asks them -- and says how many more wait in Lemma. It
is held, not sent, when the member has had too many from the pod this hour:
its lines go with their next push from that channel (``push_sends``). A push
that names them or asks them something is never held: it is what the budget
exists to keep room for. Every try of one push is sent under its delivery's key, so a mail
provider that dedupes on it (Resend) never sends one push twice.

A plain line in a group is not worth an email: it goes to a chat app the
member uses, or waits in Lemma. Email is for their own channel, a contact's,
and what names them or asks them something -- or for a member who chose it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.ports import (
    SurfaceNotificationEgressPort,
    SurfacePodMembershipPort,
)
from app.modules.agent_surfaces.services.channel_places import (
    ChannelSeats,
    ChannelSeatsInDatabase,
)
from app.modules.agent_surfaces.services.notification_channels import (
    NotificationChannelResolver,
    SurfaceProvisioner,
)
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.services.notification_delivery import (
    EMAIL_CHANNEL,
    Delivered,
    DeliveryChannel,
    Outbound,
    channel_for_platform,
)
from app.modules.agent_surfaces.services.notification_dispatch import (
    send_on_first_channel,
)
from app.modules.agent_surfaces.services.notification_egress import NotificationEgress
from app.modules.agent_surfaces.services.notification_rate_limiter import (
    EmailBudget,
    PushBudget,
)
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Member,
    SenderKind,
)

#: The most a title -- an email subject -- carries.
MAX_TITLE_CHARS = 120

#: Why a push the hourly budget held did not go, as the member's delivery says.
HELD_BY_BUDGET = (
    "They have had a lot from this pod this hour; it waits in Lemma, and goes "
    "with their next message from this channel."
)


def title_of(text: str) -> str:
    first = (text.strip().splitlines() or [""])[0]
    if len(first) <= MAX_TITLE_CHARS:
        return first or "A message for you"
    return first[: MAX_TITLE_CHARS - 1].rstrip() + "…"


def _and_more(text: str, lines: int) -> str:
    """A push standing for several lines says how many more wait in Lemma."""
    if lines <= 1:
        return text
    more = "1 more line" if lines == 2 else f"{lines - 1} more lines"
    return f"{text}\n\n(and {more} waiting in Lemma)"


def _channel_named(via: str) -> str:
    """A member's ``notify_via`` platform as the channel an agent names: every
    mail platform is ``email``."""
    if via.upper() == "EMAIL":
        return EMAIL_CHANNEL
    try:
        return channel_for_platform(SurfacePlatform(via.upper()))
    except ValueError:
        return via.lower()


@dataclass(frozen=True, slots=True)
class Push:
    """One line, to one member, from the channel it was said in."""

    channel: Channel
    line: ChannelMessage
    recipient_user_id: UUID
    #: Their membership of the channel: where they asked to be told.
    member: Member | None
    #: The key every try of this push is sent under (its delivery's).
    key: str | None = None
    #: It names them, or asks them something: never held to the budget.
    urgent: bool = False
    #: The delivery's first try, which is the one the budget is charged for.
    first_try: bool = True
    #: How many lines of the channel it stands for: the newest, and those
    #: before it since their last push from there.
    lines: int = 1

    @property
    def may_email(self) -> bool:
        """Whether email may carry it when the member chose nothing."""
        return self.urgent or self.channel.kind is not ChannelKind.GROUP


class MemberPushes:
    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        surface_repository,
        external_user_repository,
        egress: SurfaceNotificationEgressPort,
        pod_membership_port: SurfacePodMembershipPort,
        surface_provisioner: SurfaceProvisioner | None = None,
        seats: ChannelSeats | None = None,
        email_budget: EmailBudget | None = None,
        push_budget: PushBudget | None = None,
    ) -> None:
        self.uow = uow
        self.membership = pod_membership_port
        seats = seats or ChannelSeatsInDatabase(uow)
        self.channels = NotificationChannelResolver(
            seats=seats,
            surface_repository=surface_repository,
            external_user_repository=external_user_repository,
            pod_membership_port=pod_membership_port,
            surface_provisioner=surface_provisioner,
        )
        self.egress = NotificationEgress(egress=egress, uow=uow, seats=seats)
        self.email_budget = email_budget
        self.push_budget = push_budget

    async def push(self, push: Push) -> Delivered:
        """Carry the line to them; where it went, or why it went nowhere."""
        pod_id, recipient = push.channel.pod_id, push.recipient_user_id
        if await self.membership.get_pod_member_id(recipient, pod_id) is None:
            return Delivered(reason="They are no longer in this pod.")
        if not await self._within_budget(push):
            return Delivered(reason=HELD_BY_BUDGET, held=True)
        sender = push.line.sender
        agent_id = sender.agent_id if sender.kind is SenderKind.AGENT else None
        channels, reason = await self._channels(push, agent_id=agent_id)
        if not channels:
            return Delivered(reason=reason)
        return await send_on_first_channel(
            Outbound(
                id=push.line.id,
                pod_id=pod_id,
                recipient_user_id=recipient,
                title=push.channel.title or title_of(push.line.text),
                key=push.key,
            ),
            channels=channels,
            message=_and_more(await self._text(push), push.lines),
            agent_name=sender.display_name if agent_id else None,
            uow=self.uow,
            egress=self.egress,
            email_budget=self.email_budget,
        )

    async def _within_budget(self, push: Push) -> bool:
        """Whether the hourly budget lets this push go. Charged on the
        delivery's first try only: a retry is the same push."""
        if push.urgent or self.push_budget is None:
            return True
        if not push.first_try:
            return True
        return await self.push_budget.allows(
            pod_id=push.channel.pod_id, recipient_user_id=push.recipient_user_id
        )

    async def _channels(
        self, push: Push, *, agent_id: UUID | None
    ) -> tuple[list[DeliveryChannel], str]:
        """Their chosen ways, in their order; else the agent's best for them."""
        pod_id = push.channel.pod_id
        actor = effective_agent_id(agent_id, pod_id=pod_id)
        chosen = push.member.notify_via if push.member is not None else None
        if not chosen:
            return await self.channels.resolve(
                pod_id=pod_id,
                recipient_user_id=push.recipient_user_id,
                actor_agent_id=actor,
                allow_email=push.may_email,
            )
        reason = ""
        for via in chosen:
            channels, reason = await self.channels.resolve(
                pod_id=pod_id,
                recipient_user_id=push.recipient_user_id,
                actor_agent_id=actor,
                channel=_channel_named(via),
            )
            if channels:
                return channels, ""
        return [], reason

    async def _text(self, push: Push) -> str:
        """The line as it reads out of Lemma: who said it, and where, unless it
        is the pod speaking to them in their own channel."""
        line, channel = push.line, push.channel
        sender = line.sender
        if sender.kind is SenderKind.AGENT:
            behalf = sender.on_behalf_of
            if channel.kind is ChannelKind.DIRECT:
                if behalf is None or behalf.user_id in (None, push.recipient_user_id):
                    return line.text
                name = await self.membership.get_user_display_name(behalf.user_id)
                return f"On behalf of {name}:\n\n{line.text}" if name else line.text
        name = await self._name_of(line)
        where = f" in {channel.title}" if channel.title else ""
        return f"{name}{where}:\n\n{line.text}"

    async def _name_of(self, line: ChannelMessage) -> str:
        sender = line.sender
        if sender.display_name:
            return sender.display_name
        if sender.user_id is not None:
            name = await self.membership.get_user_display_name(sender.user_id)
            if name:
                return name
        return "Somebody"
