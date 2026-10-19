"""What became of one send of a channel delivery, before it is recorded.

Four ends, one per status a delivery row can settle in after a try:

* ``SENT`` -- with the platform's id for the message, when it named one;
* ``SKIPPED`` -- not sent, on purpose (a contact's rules, nowhere to send);
* ``FAILED`` -- did not go, and is worth trying again after a backoff;
* ``DEAD`` -- did not go, and trying again would repeat what already arrived
  (part of a long message) or meet the same refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt
from app.modules.agent_surfaces.platforms.delivery import SendOutcome


class Result(StrEnum):
    SENT = "SENT"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    DEAD = "DEAD"


@dataclass(frozen=True, slots=True)
class Outcome:
    result: Result
    platform: str | None = None
    reason: str | None = None
    external_message_id: str | None = None

    @property
    def sent(self) -> bool:
        return self.result is Result.SENT

    @classmethod
    def delivered(
        cls, platform: str | None, *, external_message_id: str | None = None
    ) -> Outcome:
        return cls(
            Result.SENT, platform=platform, external_message_id=external_message_id
        )

    @classmethod
    def skipped(cls, reason: str, *, platform: str | None = None) -> Outcome:
        return cls(Result.SKIPPED, platform=platform, reason=reason)

    @classmethod
    def failed(cls, reason: str, *, platform: str | None = None) -> Outcome:
        return cls(Result.FAILED, platform=platform, reason=reason)

    @classmethod
    def dead(cls, reason: str, *, platform: str | None = None) -> Outcome:
        return cls(Result.DEAD, platform=platform, reason=reason)


def of_send(
    receipt: DeliveryReceipt | None, sends: SendOutcome, *, platform: str
) -> Outcome:
    """A platform send's outcome: its receipt, and what its tries ran into."""
    if receipt is not None:
        return Outcome.delivered(
            platform, external_message_id=receipt.external_message_id
        )
    if sends.partial:
        return Outcome.dead(
            f"Only part of it reached {platform}; it was not sent again, so as "
            "not to repeat that part.",
            platform=platform,
        )
    return Outcome.failed(
        f"{platform} did not take it. It will be tried again.", platform=platform
    )


__all__ = ["Outcome", "Result", "of_send"]
