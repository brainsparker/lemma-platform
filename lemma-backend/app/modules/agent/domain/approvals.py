"""Approval resolution outcomes.

Its own module rather than a member of `value_objects`: that file is at the
architecture ratchet's size ceiling, and this is a distinct concept anyway --
the *result* of resolving an approval, as opposed to the decision a user made
(`AgentRunApprovalDecision`, which stays with the other value objects).
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple

from app.modules.agent.domain.value_objects import AgentRunApprovalDecision


class ApprovalResolutionStatus(str, Enum):
    """What resolving an approval did. Lowercase: it is the API's ``status``."""

    #: This call recorded the decision.
    RESOLVED = "resolved"
    #: The decision was already recorded; this call only finished a prior
    #: half-done resume (the self-heal path).
    RECONCILED = "reconciled"
    #: The decision is durable and a worker job owns the rest.
    QUEUED = "queued"


class ApprovalResolution(NamedTuple):
    """Approval status plus the authoritative (stored) decision."""

    status: ApprovalResolutionStatus
    decision: AgentRunApprovalDecision
