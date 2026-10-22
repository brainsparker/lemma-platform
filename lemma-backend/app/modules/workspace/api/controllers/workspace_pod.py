"""Which of a person's workspaces a request is about.

A person has one workspace per pod and actor -- the pod's assistant, or one
named agent; see ``domain/workspace_identity`` -- and one with no pod, which is
the one these routes meant before pods split them. So ``pod_id`` is optional
and absent means that personal workspace, which keeps every client that does
not send it reading what it always read. ``agent`` is optional too, and absent
means the assistant's, the one a person talking to their pod is using.

Both are checked before anything is resolved; see
``services/workspace_access``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Query

from app.modules.workspace.services.workspace_access import require_workspace_pod

#: The query parameter every person-facing workspace route takes, as
#: ``pod_id: PodQuery = None``. ``Annotated`` rather than a ``Query(...)``
#: default so the default really is ``None`` when a test calls a route
#: function directly -- the other spelling hands it the ``Query`` object, which
#: is truthy.
PodQuery = Annotated[
    UUID | None,
    Query(
        description=(
            "The pod whose workspace to use. Each pod has its own, with its "
            "own files and browser. Omitted, your personal workspace."
        ),
    ),
]


#: Which agent's workspace in the pod, as ``agent: AgentQuery = None``.
AgentQuery = Annotated[
    UUID | None,
    Query(
        description=(
            "A named agent of `pod_id`, to use that agent's workspace. Each "
            "agent has its own. Omitted, the pod assistant's."
        ),
    ),
]


__all__ = ["AgentQuery", "PodQuery", "require_workspace_pod"]
