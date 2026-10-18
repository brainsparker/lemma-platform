"""Reaching a member: a line in their own channel, carried to them on a platform.

What an agent, a workflow or the pod has to tell a member -- or ask them -- is
a line in that member's DIRECT channel. It is there whatever happens next, and
a question on it waits on them in their inbox. Then, unless they turned the
channel's notifications off or have been pushed to too often this hour, the
line is carried to them where they are: the chat app they last used, or their
email (``notification_channels`` chooses; ``notification_dispatch`` sends).

Membership fails closed: a recipient who is not in the pod is refused outright,
so no wiring bug becomes "any user id can be messaged".

A notice -- a contact who could not be answered, a bot whose keeper left -- is
a note to the member alone (``members_only``): in their channel, never pushed.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.domain.ports import (
    SurfaceNotificationEgressPort,
    SurfacePodMembershipPort,
)
from app.modules.agent_surfaces.services.channel_lines import (
    ChannelLines,
    ChannelLinesInDatabase,
)
from app.modules.agent_surfaces.services.channel_places import (
    ChannelSeats,
    ChannelSeatsInDatabase,
)
from app.modules.agent_surfaces.services.notification_channels import (
    NotificationChannelResolver,
    SurfaceProvisioner,
)
from app.modules.agent_surfaces.services.notification_delivery import (
    Delivered,
    Outbound,
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
    ChannelMessage,
    InteractionKind,
    NewQuestion,
    NotifyLevel,
    Principal,
    Sender,
    direct_channel,
    participant_of,
    post,
)
from app.modules.channels.contracts.deliveries import DeliveryStatus, record_delivery
from app.modules.channels.contracts.lines import line_said_in_run
from app.modules.channels.contracts.questions import question_for

#: What a message's question ``ref`` starts with.
MESSAGE_REF = "msg"
#: The most a title -- an email subject -- carries.
MAX_TITLE_CHARS = 120


def message_ref(conversation_id: UUID | None, key: str) -> str:
    """A message's ref: the asking conversation (``-`` for none), then a key."""
    return f"{MESSAGE_REF}:{conversation_id or '-'}:{key}"


def asking_conversation(ref: str) -> UUID | None:
    kind, _, rest = ref.partition(":")
    conversation, _, _key = rest.partition(":")
    if kind != MESSAGE_REF or conversation in ("", "-"):
        return None
    try:
        return UUID(conversation)
    except ValueError:
        return None


def attribute(body: str, *, actor_display_name: str | None) -> str:
    """Say who the agent acts for, when it is not the recipient themselves."""
    if not actor_display_name:
        return body
    return f"On behalf of {actor_display_name}:\n\n{body}"


def title_of(text: str) -> str:
    first = (text.strip().splitlines() or [""])[0]
    if len(first) <= MAX_TITLE_CHARS:
        return first or "A message for you"
    return first[: MAX_TITLE_CHARS - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class DirectMessage:
    pod_id: UUID
    recipient_user_id: UUID
    text: str
    #: Who the agent acts for; the recipient's own run names nobody else.
    actor_user_id: UUID | None = None
    actor_agent_id: UUID | None = None
    agent_name: str | None = None
    origin_conversation_id: UUID | None = None
    origin_run_id: UUID | None = None
    #: The surface the asking run answers on: the same bot reaches out.
    origin_surface_id: UUID | None = None
    #: A platform the agent chose on purpose: honoured or refused, never swapped.
    platform: str | None = None
    wants_answer: bool = False
    #: Makes a retried send the first one, not a second line.
    key: str | None = None
    title: str | None = None


@dataclass(frozen=True, slots=True)
class DirectResult:
    line: ChannelMessage
    delivered: Delivered
    repeated: bool = False


class DirectMessages:
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
        lines: ChannelLines | None = None,
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
        self.egress = NotificationEgress(
            egress=egress,
            uow=uow,
            seats=seats,
            lines=lines or ChannelLinesInDatabase(uow),
        )
        self.email_budget = email_budget
        self.push_budget = push_budget

    async def send(self, message: DirectMessage) -> DirectResult:
        """Post the line, then carry it to them. Commits the line first."""
        pod_id, recipient = message.pod_id, message.recipient_user_id
        if await self.membership.get_pod_member_id(recipient, pod_id) is None:
            raise AgentSurfaceValidationError(
                "The recipient is not a member of this pod."
            )
        ref = message_ref(message.origin_conversation_id, message.key or uuid4().hex)
        channel = await direct_channel(self.uow, pod_id=pod_id, user_id=recipient)
        if message.key and (earlier := await self._earlier(channel.id, ref, message)):
            return DirectResult(line=earlier, delivered=Delivered(), repeated=True)
        agent_id = effective_agent_id(message.actor_agent_id, pod_id=pod_id)
        line = await post(
            self.uow,
            channel_id=channel.id,
            sender=Sender.agent(
                agent_id,
                on_behalf_of=Principal.member(message.actor_user_id or recipient),
                display_name=message.agent_name,
            ),
            text=message.text,
            conversation_id=message.origin_conversation_id,
            agent_run_id=message.origin_run_id,
            question=(
                NewQuestion(
                    kind=InteractionKind.ASK,
                    question={
                        "ref": ref,
                        "tool": "message",
                        "prompt": message.text,
                    },
                    awaits=Principal.member(recipient),
                )
                if message.wants_answer
                else None
            ),
        )
        if line is None:
            raise AgentSurfaceValidationError("There is nothing to send.")
        await self.uow.commit()
        delivered = await self.deliver(line, message=message)
        await self.uow.commit()
        return DirectResult(line=line, delivered=delivered)

    async def _earlier(
        self, channel_id: UUID, ref: str, message: DirectMessage
    ) -> ChannelMessage | None:
        """The line a retried job already wrote: by its question, or by run."""
        if message.wants_answer:
            return await question_for(self.uow, ref)
        if message.origin_run_id is None:
            return None
        return await line_said_in_run(
            self.uow,
            channel_id=channel_id,
            agent_run_id=message.origin_run_id,
            text=message.text,
        )

    async def deliver(
        self, line: ChannelMessage, *, message: DirectMessage
    ) -> Delivered:
        pod_id, recipient = message.pod_id, message.recipient_user_id
        seat = await participant_of(
            self.uow, channel_id=line.channel_id, principal=Principal.member(recipient)
        )
        if seat is not None and seat.notify is NotifyLevel.NONE:
            return await self._skipped(
                line, message, "They read this channel in Lemma only."
            )
        if self.push_budget is not None and not await self.push_budget.allows(
            pod_id=pod_id, recipient_user_id=recipient
        ):
            return await self._skipped(
                line,
                message,
                "They have had a lot from this pod this hour; it waits in Lemma.",
            )
        channels, fallback = await self.channels.resolve(
            pod_id=pod_id,
            recipient_user_id=recipient,
            actor_agent_id=message.actor_agent_id,
            origin_surface_id=message.origin_surface_id,
            agent_name=message.agent_name,
            channel=message.platform,
        )
        if not channels:
            return await self._skipped(line, message, fallback)
        actor = message.actor_user_id
        actor_name = (
            await self.membership.get_user_display_name(actor)
            if actor and actor != recipient
            else None
        )
        return await send_on_first_channel(
            Outbound(
                id=line.id,
                pod_id=pod_id,
                recipient_user_id=recipient,
                title=message.title or title_of(message.text),
            ),
            channels=channels,
            message=attribute(message.text, actor_display_name=actor_name),
            agent_name=message.agent_name,
            uow=self.uow,
            egress=self.egress,
            email_budget=self.email_budget,
        )

    async def _skipped(
        self, line: ChannelMessage, message: DirectMessage, reason: str
    ) -> Delivered:
        await record_delivery(
            self.uow,
            message_id=line.id,
            pod_id=message.pod_id,
            platform=message.platform or "LEMMA",
            status=DeliveryStatus.SKIPPED,
            recipient_user_id=message.recipient_user_id,
            error=reason,
        )
        return Delivered(reason=reason)


async def tell_member(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, user_id: UUID, text: str
) -> ChannelMessage | None:
    """A notice to one member: a note in their own channel, never pushed."""
    channel = await direct_channel(uow, pod_id=pod_id, user_id=user_id)
    agent_id = effective_agent_id(None, pod_id=pod_id)
    return await post(
        uow,
        channel_id=channel.id,
        sender=Sender.agent(agent_id, on_behalf_of=Principal.member(user_id)),
        text=text,
        members_only=True,
    )
