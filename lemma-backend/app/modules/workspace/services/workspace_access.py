"""Whether a person may name one of their workspaces by pod and agent.

A person's workspace in a pod is theirs whichever pod is named, so naming one
is not a way into anybody else's. It is still checked: an unchecked pod id
would let anyone start a sandbox -- a container, on the platform's account --
for every id they cared to invent, and would leave a person who has been taken
out of a pod watching that pod's browser. An agent is checked to be the pod's,
for the first reason.

Here rather than beside the routes because two modules' routes ask it: the
workspace's own, and ``web_login``'s, which manage the same browsers.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.delegation import is_pod_default_agent
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.live.pod_access import require_pod_access


async def require_workspace_pod(
    user_id: UUID, pod_id: UUID | None, agent: UUID | None = None
) -> None:
    """Raise the ``DomainError`` a read of the pod would, if they cannot see it,
    and a 404 for an agent that is not the pod's."""
    if pod_id is None:
        if agent is not None:
            # An agent belongs to a pod; without one this names nothing.
            raise DomainError(
                "An agent's workspace is named with its pod_id",
                code="WORKSPACE_AGENT_WITHOUT_POD",
                status_code=422,
            )
        return
    await require_pod_access(pod_id, user_id)
    if is_pod_default_agent(agent, pod_id=pod_id):
        return
    from app.modules.agent.contracts.agents import agent_name_in_pod

    async with async_session_maker() as session:
        await agent_name_in_pod(session, pod_id=pod_id, agent_id=agent)


async def conversation_workspace(
    user_id: UUID, conversation_id: UUID
) -> tuple[UUID, UUID | None] | None:
    """The pod and agent of this person's conversation, or ``None`` if it is
    not theirs.

    How a view opened for a conversation -- a sign-in link -- finds the browser
    the conversation's agent is using, without the page having to name it.
    """
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.agent.contracts.conversations_for_surfaces import (
        surface_conversation,
    )

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        found = await surface_conversation(uow, conversation_id)
    if found is None or found.user_id != user_id:
        return None
    return found.pod_id, found.agent_id


async def choose_view_workspace(
    user_id: UUID,
    *,
    pod_id: UUID | None,
    agent: UUID | None,
    conversation: UUID | None,
) -> tuple[UUID | None, UUID | None]:
    """The ``(pod, agent)`` whose browser a view socket opens, checked.

    Named outright when the page names it; otherwise by the conversation it was
    opened for, which is all a sign-in link carries. Raises the ``DomainError``
    a read would for a pod or agent the person may not name.
    """
    if pod_id is None and agent is None and conversation is not None:
        found = await conversation_workspace(user_id, conversation)
        if found is not None:
            pod_id, agent = found
    await require_workspace_pod(user_id, pod_id, agent)
    return pod_id, agent


__all__ = [
    "choose_view_workspace",
    "conversation_workspace",
    "require_workspace_pod",
]
