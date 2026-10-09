"""What made a cached platform sender somebody in Lemma.

The external-user cache used to record who a sender resolved to and never how,
so every reader had to re-derive whether the answer could be believed -- and a
row written on a Telegram handle alone looked exactly like one written on a
verified number. A handle is free text on a profile that nobody confirms, so it
is never proof; recording the proof is what lets a proven Telegram account stay
resolved while an unproven one is asked to confirm.
"""

from __future__ import annotations

from enum import StrEnum


class SenderProof(StrEnum):
    #: The platform attested the sender's number (WhatsApp's signed sender, a
    #: self-shared Telegram contact) and it matched a profile's number.
    PHONE = "PHONE"
    #: The platform's own API returned the profile email (Slack, Teams).
    EMAIL = "EMAIL"
    #: The sender opened a one-time setup link a signed-in Lemma user started,
    #: from the account that user's profile names (managed Telegram bots).
    SETUP_LINK = "SETUP_LINK"
