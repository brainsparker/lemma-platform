"""The surfaces module's work kinds, by name, before their handlers exist.

Inbound ingest, outbound delivery and the receivers move onto the work
runtime in pieces that land in either order; each names the kinds and builds
the dedup keys through these constants, so a writer and the handler
registration cannot disagree on a string. Constants only: importing this
module registers nothing.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

#: Routes one stored inbound message into a conversation.
SURFACE_INGEST: Final = "surface.ingest"
#: Sends one ``surface_deliveries`` row to its platform.
SURFACE_REPLY: Final = "surface.reply"
#: Posts a finished run's answer as a line with a delivery (an ``AgentRunFinished`` subscriber).
SURFACE_REPLY_FOR_RUN: Final = "surface.reply_for_run"
#: Sends one channel delivery: a line to a place, or a push to a member.
SURFACE_CHANNEL_DELIVERY: Final = "surface.channel_delivery"
#: A perpetual poller for one credential (Telegram, Slack socket, Resend).
SURFACE_RECEIVER: Final = "surface.receiver"
#: Provider-side follow-ups of a webhook that is not a message (statuses, lifecycle).
SURFACE_PLATFORM_UPDATE: Final = "surface.platform_update"
#: Clears identities bound to a phone number a user no longer has.
SURFACE_MOBILE_CHANGED: Final = "surfaces.mobile_changed"

#: Periodic names (``periodic(name, every, kind)``).
RECEIVERS_RECONCILE_PERIODIC: Final = "surfaces.receivers.reconcile"
ONBOARDING_CLEANUP_PERIODIC: Final = "surfaces.onboarding_cleanup"


def ingest_key(inbound_message_id: UUID) -> str:
    return f"ingest:{inbound_message_id}"


def run_reply_key(run_id: UUID) -> str:
    return f"run-reply:{run_id}"


def channel_delivery_key(delivery_id: UUID) -> str:
    return f"channel-delivery:{delivery_id}"


def channel_delivery_target_key(target: str, delivery_id: UUID) -> str:
    """One target's sends run one at a time (``Delivery.target_key``)."""
    return f"channel-delivery-target:{target or delivery_id}"


def receiver_key(credential_key: str) -> str:
    return f"receiver:{credential_key}"


__all__ = [
    "ONBOARDING_CLEANUP_PERIODIC",
    "RECEIVERS_RECONCILE_PERIODIC",
    "SURFACE_INGEST",
    "SURFACE_CHANNEL_DELIVERY",
    "SURFACE_MOBILE_CHANGED",
    "SURFACE_PLATFORM_UPDATE",
    "SURFACE_RECEIVER",
    "SURFACE_REPLY",
    "SURFACE_REPLY_FOR_RUN",
    "ingest_key",
    "channel_delivery_key",
    "channel_delivery_target_key",
    "receiver_key",
    "run_reply_key",
]
