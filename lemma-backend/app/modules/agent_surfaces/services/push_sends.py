"""A channel line pushed to one member: the send, and what it ends as.

``MemberPushes`` chooses where the member is reached and sends; this turns
what happened into the delivery's outcome. Sent is ``SENT``; held by the
hourly budget is ``SKIPPED`` and counted into a digest (``push_digests``); a
platform asked that took none of it ends as its failure says
(``delivery_outcome.of_send``) -- so a push that may be on their screen is not
tried again -- and nowhere to reach them is ``SKIPPED``: it waits in Lemma.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.delivery import track_sends
from app.modules.agent_surfaces.services.delivery_outcome import Outcome, of_send
from app.modules.agent_surfaces.services.member_pushes import MemberPushes, Push
from app.modules.agent_surfaces.services.notification_delivery import Delivered
from app.modules.agent_surfaces.services.push_digests import (
    HELD_FOR_DIGEST,
    hold_for_digest,
)
from app.modules.agent_surfaces.services.run_answer_sends import delivery_key
from app.modules.channels.contracts import (
    Channel,
    ChannelMessage,
    Delivery,
    Principal,
    member_of,
)
from app.modules.channels.contracts.deliveries import push_is_urgent

#: The pushes over one unit of work.
type PushesFor = Callable[[SqlAlchemyUnitOfWork], MemberPushes]


async def push_outcome(
    uow_factory: UnitOfWorkFactory,
    pushes_for: PushesFor,
    *,
    delivery: Delivery,
    channel: Channel,
    line: ChannelMessage,
) -> Outcome:
    """Push the line to the delivery's member; what became of it."""
    recipient = delivery.recipient_user_id
    if recipient is None:  # pragma: no cover - a CHECK holds it to one target
        return Outcome.skipped("This delivery names nobody.")
    raised: BaseException | None = None
    delivered = Delivered(reason="The push did not go.")
    with track_sends() as sends:
        async with uow_factory() as uow:
            member = await member_of(
                uow,
                channel_id=channel.id,
                principal=Principal.member(recipient),
            )
            urgent = await push_is_urgent(uow, delivery)
            try:
                delivered = await pushes_for(uow).push(
                    Push(
                        channel=channel,
                        line=line,
                        recipient_user_id=recipient,
                        member=member,
                        key=delivery_key(delivery),
                        urgent=urgent,
                        # Charged once per delivery, not per try of it.
                        first_try=delivery.attempts <= 1,
                    )
                )
            except platform_transport_errors() as exc:
                raised = exc
            if delivered.held:
                await hold_for_digest(
                    uow,
                    pod_id=channel.pod_id,
                    recipient_user_id=recipient,
                    channel_id=channel.id,
                )
            await uow.commit()
    if delivered.sent:
        return Outcome.delivered(
            delivered.platform, external_message_id=delivered.external_message_id
        )
    if delivered.held:
        return Outcome.skipped(HELD_FOR_DIGEST)
    if sends.tried:
        # A platform was asked and took none of it: whether to try again
        # is what that platform's failure says (``of_send``).
        return of_send(None, sends, platform=sends.platform or "The platform")
    if raised is not None or delivered.retryable:
        return Outcome.failed(delivered.reason or "The push did not go.")
    # Nowhere to reach them is a fact about them, not an outage: the line
    # waits in Lemma, and trying again changes nothing.
    return Outcome.skipped(delivered.reason or "Nothing could reach them.")


__all__ = ["PushesFor", "push_outcome"]
