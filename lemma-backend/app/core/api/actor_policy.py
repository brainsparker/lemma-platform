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
        # Reading what the org has installed. Running an operation goes
        # through a pod connector under /pods/{pod_id}, where the pod pin and
        # the workload's grants apply (and the deprecated route below).
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
        # Deprecated, kept so code written before pod connectors keeps running:
        # executing by installation name resolves through the token's pod and
        # its connectors (``execute_installation_operation``), the same checks
        # as the pod route, and searching operations across installs is a read.
        (
            "POST",
            (
                "/organizations/{organization_id}/connectors/{auth_config_name}"
                "/operations/{operation_name}/execute"
            ),
        ),
        ("GET", "/organizations/{organization_id}/connector-operations"),
        # An agent hands its person a link to connect an account; the link is
        # theirs and completes only in their browser.
        ("POST", "/organizations/{organization_id}/connectors/connect-requests"),
        # Which models an agent may be configured with.
        ("GET", "/organizations/{organization_id}/agent-runtime/profiles"),
        ("GET", "/organizations/{organization_id}/agent-runtime/profiles/{profile_id}"),
        # A function's own runtime: fetching its code and reporting how its run
        # ended. The controller holds the token to that function and run.
        (
            "GET",
            (
                "/internal/function-runtime/functions/{function_id}"
                "/artifacts/{revision_hash}"
            ),
        ),
        ("POST", "/internal/function-runtime/runs/{run_id}:terminal"),
        # Tools the sandbox calls back for.
        ("POST", "/tools/web-search"),
        ("POST", "/tools/report-feedback"),
    }
)

_ORG_CONNECTORS = "/organizations/{organization_id}/connectors"

#: Routes outside a pod that a pod app's page may call (H20, ``core/app_pin``).
#: The page acts as its viewer with their whole access, but only in its own
#: pod; outside it, only who is viewing, the connector catalogue, and setting
#: a connector up in its own pod's organization -- so an app can onboard a
#: person into a connector. Each of these checks the viewer's own permission.
#: Anything else (org settings, members, billing, other pods) is refused.
APP_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/users/me"),
        ("GET", "/users/me/profile"),
        ("GET", "/connectors"),
        ("GET", "/connectors/{connector_id}"),
        ("GET", "/connectors/{connector_id}/skill"),
        # Auth configs: create, list, get, update, refresh.
        ("POST", f"{_ORG_CONNECTORS}/auth-configs"),
        ("GET", f"{_ORG_CONNECTORS}/auth-configs"),
        ("GET", f"{_ORG_CONNECTORS}/auth-configs/{{auth_config_name}}"),
        ("PATCH", f"{_ORG_CONNECTORS}/auth-configs/{{auth_config_name}}"),
        (
            "POST",
            f"{_ORG_CONNECTORS}/auth-configs/{{auth_config_name}}/operations/refresh",
        ),
        # Accounts: create, list, get, update, delete, GitHub installations.
        ("POST", f"{_ORG_CONNECTORS}/accounts"),
        ("GET", f"{_ORG_CONNECTORS}/accounts"),
        ("GET", f"{_ORG_CONNECTORS}/accounts/{{account_id}}"),
        ("PATCH", f"{_ORG_CONNECTORS}/accounts/{{account_id}}"),
        ("DELETE", f"{_ORG_CONNECTORS}/accounts/{{account_id}}"),
        ("GET", f"{_ORG_CONNECTORS}/accounts/{{account_id}}/github/installations"),
        ("POST", f"{_ORG_CONNECTORS}/accounts/{{account_id}}/github/installations"),
        # Connect requests and installs.
        ("POST", f"{_ORG_CONNECTORS}/connect-requests"),
        ("POST", f"{_ORG_CONNECTORS}/connect-requests/install"),
        # Operation discovery, details and search.
        ("GET", f"{_ORG_CONNECTORS}/{{auth_config_name}}/operations"),
        (
            "GET",
            f"{_ORG_CONNECTORS}/{{auth_config_name}}/operations/{{operation_name}}",
        ),
        ("POST", f"{_ORG_CONNECTORS}/{{auth_config_name}}/operations/details"),
        ("GET", "/organizations/{organization_id}/connector-operations"),
        # Deprecated execute by installation name, as for workloads.
        (
            "POST",
            (
                f"{_ORG_CONNECTORS}/{{auth_config_name}}"
                "/operations/{operation_name}/execute"
            ),
        ),
        # Which pods may use an account the viewer connected.
        ("GET", "/me/connectors/accounts/{account_id}/pods"),
        # The models a chat may run on: the SDK's assistant UI lists them
        # before a chat, so an app's chat keeps working as on main.
        ("GET", "/organizations/{organization_id}/agent-runtime/profiles"),
        ("GET", "/organizations/{organization_id}/agent-runtime/profiles/{profile_id}"),
        # The app host's own door: a ticket for this host's access cookie. It
        # names the app by ``Origin`` itself (``apps/api/app_access_controller``).
        ("POST", "/apps/access/tickets"),
        # The app's own organization and its people, as the viewer: internal
        # apps pick colleagues (`useOrganizationMembers`) and onboard them --
        # invite, change a role, remove -- and each route already asks the
        # viewer's own permission, so an app does what its viewer may and
        # nothing more. The organization's own settings stay out.
        ("GET", "/organizations/{organization_id}"),
        ("GET", "/organizations/{organization_id}/permissions/me"),
        ("GET", "/organizations/{organization_id}/members"),
        ("PATCH", "/organizations/{organization_id}/members/{member_id}/role"),
        ("DELETE", "/organizations/{organization_id}/members/{member_id}"),
        ("GET", "/organizations/{organization_id}/invitations"),
        ("POST", "/organizations/{organization_id}/invitations"),
        # Connector reads an onboarding app shows alongside setup.
        ("GET", f"{_ORG_CONNECTORS}/status"),
        ("GET", f"{_ORG_CONNECTORS}/{{auth_config_name}}/triggers"),
        (
            "GET",
            f"{_ORG_CONNECTORS}/{{auth_config_name}}/triggers/{{trigger_name}}",
        ),
    }
)

WORKLOAD_NOT_ALLOWED = "WORKLOAD_NOT_ALLOWED"
APP_ROUTE_NOT_ALLOWED = "APP_ROUTE_NOT_ALLOWED"


def workload_may_call(method: str, path_template: str) -> bool:
    """Whether a workload's token may call this route at all."""
    if _is_pod_route(path_template):
        return True
    return (method.upper(), path_template) in WORKLOAD_ROUTES


def _is_pod_route(path_template: str) -> bool:
    return path_template == POD_SCOPED_PREFIX or path_template.startswith(
        POD_SCOPED_PREFIX + "/"
    )


def app_may_call(method: str, path_template: str) -> bool:
    """Whether a pod app's page may call this route at all. Which pod and which
    organization it names is ``core/app_pin``'s to check."""
    if _is_pod_route(path_template):
        return True
    return (method.upper(), path_template) in APP_ROUTES


def refuse_app_route(method: str, path_template: str | None) -> DomainError:
    route = f"{method.upper()} {path_template or 'an unmatched route'}"
    return DomainError(
        f"A pod app's page may not call {route}: an app reaches its own pod, "
        "who is viewing it, and connector setup in its own organization.",
        code=APP_ROUTE_NOT_ALLOWED,
        status_code=403,
        details={"method": method.upper(), "route": path_template},
    )


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


def refuse_named_workloads(action_label: str):
    """Close a pod route to named agents and functions, but not the assistant.

    For work done as the person in a background job -- exporting, importing or
    publishing the whole pod -- which cannot carry a workload's grants into the
    job. The pod's assistant acts as its person anyway, so it keeps them; a named
    agent holding only ``pod.read`` used to export everything its person could
    read.
    """
    from app.core.authorization.delegation import is_default_pod_agent_claims

    async def _dependency(request: Request) -> None:
        claims = getattr(request.state, "delegation_claims", None)
        if claims is not None and not is_default_pod_agent_claims(claims):
            raise refuse_workload(action_label)

    return Depends(_dependency)


__all__ = [
    "APP_ROUTE_NOT_ALLOWED",
    "APP_ROUTES",
    "app_may_call",
    "refuse_app_route",
    "refuse_named_workloads",
    "POD_SCOPED_PREFIX",
    "WORKLOAD_NOT_ALLOWED",
    "WORKLOAD_ROUTES",
    "refuse_workload",
    "require_person",
    "route_template",
    "workload_may_call",
]
