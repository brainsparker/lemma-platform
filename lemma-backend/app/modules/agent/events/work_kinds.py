"""The agent module's work kinds, by name, before their handlers exist.

Agent turns, approvals and waits move onto the work runtime in separate
pieces of work that land in either order. Each names the kinds -- and builds
the dedup keys -- through these constants, so a writer (``WorkWriter.add``),
a canceller (``WorkWriter.cancel``) and the handler registration can never
disagree on a string. Constants only: importing this module registers nothing.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

#: One turn of a conversation: runs the harness until the run ends or pauses.
AGENT_TURN: Final = "agent.turn"
#: Names a conversation after its first exchange.
AGENT_TITLE: Final = "agent.title"
#: Starts the run for messages queued while the previous one was busy.
AGENT_FOLLOWUP: Final = "agent.followup"
#: Hands a finished sub-agent's result to a parent that is not waiting on it.
AGENT_SUBAGENT_RESULT: Final = "agent.subagent_result"
#: Brings an asking conversation back once every notification it sent settled.
AGENT_REPLIES_ARRIVED: Final = "agent.replies_arrived"
#: Executes an approved tool call and resumes the conversation.
AGENT_APPROVAL_EXECUTE: Final = "agent.approval.execute"
#: A ``wait_for`` timer: fires a TIME wait, polls a PROCESS one.
AGENT_WAIT_FIRE: Final = "agent.wait.fire"
#: Resolves a parent's SUBAGENT wait when the child run finishes.
AGENT_WAIT_RESOLVE_CHILD: Final = "agent.wait.resolve_child"
#: Delivers a pod-to-pod ask's answer into the asking pod.
POD_ASK_DELIVER: Final = "pod_ask.deliver"


def turn_key(run_id: UUID) -> str:
    """The ``agent.turn`` dedup key: stop cancels the queued turn by it."""
    return f"agent-run:{run_id}"


def title_key(conversation_id: UUID) -> str:
    return f"conv-title:{conversation_id}"


def followup_key(run_id: UUID) -> str:
    return f"followup:{run_id}"


def subagent_result_key(run_id: UUID) -> str:
    return f"subagent-result:{run_id}"


def approval_key(conversation_id: UUID, approval_id: UUID) -> str:
    """Both racing deciders write this; the queued item coalesces them."""
    return f"agent-approval:{conversation_id}:{approval_id}"


def wait_key(wait_id: UUID) -> str:
    """The ``agent.wait.fire`` dedup key: cancelling a wait drops its timer by it."""
    return f"wait:{wait_id}"


def child_wait_key(run_id: UUID) -> str:
    return f"child-wait:{run_id}"


def pod_ask_key(run_id: UUID) -> str:
    return f"pod-ask:{run_id}"


__all__ = [
    "AGENT_APPROVAL_EXECUTE",
    "AGENT_FOLLOWUP",
    "AGENT_REPLIES_ARRIVED",
    "AGENT_SUBAGENT_RESULT",
    "AGENT_TITLE",
    "AGENT_TURN",
    "AGENT_WAIT_FIRE",
    "AGENT_WAIT_RESOLVE_CHILD",
    "POD_ASK_DELIVER",
    "approval_key",
    "child_wait_key",
    "followup_key",
    "pod_ask_key",
    "subagent_result_key",
    "title_key",
    "turn_key",
    "wait_key",
]
