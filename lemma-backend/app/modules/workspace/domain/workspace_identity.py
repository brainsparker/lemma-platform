"""Which workspace sandbox a run executes in.

**One per person, per pod, per actor** -- the pod's assistant, or one named
agent -- because the sandbox is the trust boundary and nothing narrower is.
Every process in a sandbox runs as the same UID over the same home directory.
A shell is handed its run's delegated token in its environment, and any other
process of that UID reads it from ``/proc/<pid>/environ``; moving the token
into a file, a socket or a second variable changes where it is read from, not
who can read it. Even a separate UID per run would not hold while the home is
shared: a run that can write ``~/.bashrc``, a ``~/.local/bin`` shim or a
``usercustomize`` module runs its code inside the next run's shell, with that
run's token. So whatever must not reach a token must not share its sandbox.

A run's token is pinned to its pod and intersected with its agent's grants.
A sandbox shared across pods let a run in one pod -- or one org -- lift a live
token minted for another. One shared across the agents of a pod let a named
agent, restricted by its grants, lift the assistant's, which acts with the
person's own authority there. Hence all three in the key.

Runs of the same actor still share a sandbox, and with it each other's
tokens. They hold the same grants, so there is nothing for one to gain from
the other's.

**The id is derived, not stored.** Like a host sandbox's (see
``host_execution``), a pod workspace's id is a tagged UUIDv8 computed from who,
where and as whom, so every caller arrives at the same sandbox without a
lookup, and the id alone says it is a pod workspace -- a miss on one can be
refused rather than mistaken for a person's first visit. A workspace with no
pod keeps the id it always had, the person's own user id, which is what keeps
the disk that predates this split addressable.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from app.core.authorization.delegation import effective_agent_id
from app.modules.workspace.domain.sandbox import DEFAULT_SLUG

#: The first six bytes of every pod workspace id. Distinct from the host
#: sandbox tag, so neither is ever routed as the other.
_TAG = b"lmpodw"

#: Workloads whose own id names their sandbox. Anything else that opens a
#: session in a pod -- an app build the person asked for -- is the pod's
#: assistant's.
_FUNCTION_WORKLOAD = "function"
_AGENT_WORKLOAD = "agent"


def workspace_actor(pod_id: UUID, agent_id: UUID | None) -> UUID:
    """The actor whose sandbox this is, with the assistant's ids normalised.

    The assistant has been named by ``None``, a sentinel and its pod's id
    across eras; all three must land in the same sandbox, or a run of the
    assistant could find itself beside a run of nobody in particular.
    """
    return effective_agent_id(agent_id, pod_id=pod_id)


def workload_actor(
    pod_id: UUID, workload_type: str | None, workload_id: UUID | None
) -> UUID:
    """The actor a session minted for this workload runs as."""
    kind = (workload_type or "").lower()
    if kind == _FUNCTION_WORKLOAD and workload_id is not None:
        return workload_id
    if kind == _AGENT_WORKLOAD:
        return workspace_actor(pod_id, workload_id)
    return workspace_actor(pod_id, None)


def workspace_sandbox_id(
    user_id: UUID, pod_id: UUID | None, actor_id: UUID | None = None
) -> UUID:
    """The sandbox ``user_id``'s work in ``pod_id``, as ``actor_id``, runs in.

    No pod is the person's personal workspace, whose id is their user id. No
    actor is the pod's assistant.
    """
    if pod_id is None:
        return user_id
    actor = workspace_actor(pod_id, actor_id)
    digest = hashlib.sha256(
        b"lemma-pod-workspace:" + user_id.bytes + pod_id.bytes + actor.bytes
    )
    raw = bytearray(_TAG + digest.digest()[: 16 - len(_TAG)])
    raw[6] = (raw[6] & 0x0F) | 0x80  # version 8
    raw[8] = (raw[8] & 0x3F) | 0x80  # RFC 9562 variant
    return UUID(bytes=bytes(raw))


def is_pod_workspace_id(sandbox_id: UUID) -> bool:
    return sandbox_id.version == 8 and sandbox_id.bytes[: len(_TAG)] == _TAG


def workspace_slug(sandbox_id: UUID, pod_id: UUID | None) -> str:
    """The row's slug, unique per owner, so the row is found as well as made.

    Named after the sandbox rather than the pod and actor: the slug column is
    64 characters, and two ids do not fit beside a prefix.
    """
    if pod_id is None:
        return DEFAULT_SLUG
    return f"ws-{sandbox_id.hex}"


__all__ = [
    "is_pod_workspace_id",
    "workload_actor",
    "workspace_actor",
    "workspace_sandbox_id",
    "workspace_slug",
]
