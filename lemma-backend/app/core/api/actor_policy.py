"""Which routes a workload's token may call at all.

``verify_auth`` admits a delegated token -- an agent or function acting for a
person -- on every authenticated route, because the token *is* that person's
session with claims attached. Whether the claims then narrowed anything used to
depend on the route: one that built a pod or org ``Context`` applied them, and
one that read only ``request.state.user`` treated the workload as the person.
Workloads were kept off the second kind route by route, with a denylist, so
every route nobody remembered stayed open -- three of them minted fresh,
unrestricted sessions for the person behind the token.

Inverted here. A workload may call:

- any route under ``/pods/{pod_id}``, where the pod context applies its claims
  and pins it to its own pod, unless that route requires a person
  (:func:`require_person`);
- the few routes outside a pod listed in :data:`WORKLOAD_ROUTES`, each of which
  applies the claims itself;

and nothing else. A new route is closed to workloads until someone opens it.
"""

from __future__ import annotations

from fastapi import Depends, Request
from starlette.requests import HTTPConnection

from app.core.domain.errors import DomainError

#: The pod prefix every pod-scoped route shares.
POD_SCOPED_PREFIX = "/pods/{pod_id}"

#: Routes outside a pod that a workload may call, as ``(method, path template)``.
#: Each resolves the workload's own context (or reads nothing beyond the person's
#: own profile), so opening it does not hand the workload the person.
WORKLOAD_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # Who am I acting for -- the CLI in a sandbox asks before anything else.
        ("GET", "/users/me"),
        ("GET", "/users/me/profile"),
        # Where it is: the organization its pod belongs to. Reading only; every
        # route that changes the organization stays closed.
        ("GET", "/organizations/{organization_id}"),
        # The connector catalogue: public knowledge.
        ("GET", "/connectors"),
        ("GET", "/connectors/{connector_id}"),
        ("GET", "/connectors/{connector_id}/skill"),
        # Using connectors. Execution resolves the account through the
        # workload's own context (connector.use, account grants, intersection).
        ("GET", "/organizations/{organization_id}/connectors/status"),
        ("GET", "/organizations/{organization_id}/connectors/accounts"),
        ("GET", "/organizations/{organization_id}/connectors/accounts/{account_id}"),
        ("GET", "/organizations/{organization_id}/connectors/auth-configs"),
        (
            "GET",
            "/organizations/{organization_id}/connectors/auth-configs/{auth_config_name}",
        ),
        (
            "GET",
            "/organizations/{organization_id}/connectors/{auth_config_name}/operations",
        ),
        (
            "GET",
            (
                "/organizations/{organization_id}/connectors/{auth_config_name}"
                "/operations/{operation_name}"
            ),
        ),
        (
            "POST",
            (
                "/organizations/{organization_id}/connectors/{auth_config_name}"
                "/operations/details"
            ),
        ),
        (
            "POST",
            (
                "/organizations/{organization_id}/connectors/{auth_config_name}"
                "/operations/{operation_name}/execute"
            ),
        ),
        (
            "GET",
            "/organizations/{organization_id}/connectors/{auth_config_name}/triggers",
        ),
        (
            "GET",
            (
                "/organizations/{organization_id}/connectors/{auth_config_name}"
                "/triggers/{trigger_name}"
            ),
        ),
        # An agent hands its person a link to connect an account; the link is
        # theirs and completes only in their browser.
        ("POST", "/organizations/{organization_id}/connectors/connect-requests"),
        # Which models an agent may be configured with.
        ("GET", "/organizations/{organization_id}/agent-runtime/profiles"),
        ("GET", "/organizations/{organization_id}/agent-runtime/profiles/{profile_id}"),
        # Tools the sandbox calls back for.
        ("POST", "/tools/web-search"),
        ("POST", "/tools/report-feedback"),
    }
)

WORKLOAD_NOT_ALLOWED = "WORKLOAD_NOT_ALLOWED"


def workload_may_call(method: str, path_template: str) -> bool:
    """Whether a workload's token may call this route at all."""
    if path_template == POD_SCOPED_PREFIX or path_template.startswith(
        POD_SCOPED_PREFIX + "/"
    ):
        return True
    return (method.upper(), path_template) in WORKLOAD_ROUTES


def refuse_workload(action_label: str) -> DomainError:
    return DomainError(
        f"Delegated workloads may not {action_label}.",
        code=WORKLOAD_NOT_ALLOWED,
        status_code=403,
    )


def route_template(connection: HTTPConnection) -> str | None:
    """The matched route's path template, e.g. ``/pods/{pod_id}/agents``."""
    route = connection.scope.get("route")
    return getattr(route, "path_format", None) or getattr(route, "path", None)


def require_person(action_label: str):
    """Close one pod-scoped route to workloads.

    Pod routes are open to workloads by default; this is for the few that must
    be a person's own act whatever a workload was granted or approved for --
    conferring membership, opening a function to people outside the pod.
    """

    async def _dependency(request: Request) -> None:
        if getattr(request.state, "delegation_claims", None) is not None:
            raise refuse_workload(action_label)

    return Depends(_dependency)


__all__ = [
    "POD_SCOPED_PREFIX",
    "WORKLOAD_NOT_ALLOWED",
    "WORKLOAD_ROUTES",
    "refuse_workload",
    "require_person",
    "route_template",
    "workload_may_call",
]
