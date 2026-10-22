"""Authorization conformance: who may call what, checked against the real API.

The spine of the authorization revamp. Each step adds the rows for what it
closes; a row is tagged with the audit finding it proves, so ``-k C2`` runs that
finding's proof.

This first part is the route policy (``app/core/api/actor_policy.py``): a
workload's token -- the pod's assistant or a named agent, acting for a person --
reaches only pod routes and the few routes outside a pod opened to it. Routes
are enumerated from the running app rather than listed, so a route added later
is covered the day it lands.
"""

from __future__ import annotations

import asyncio
import json
import re
from uuid import UUID, uuid4, uuid7

import pytest
from httpx import AsyncClient
from starlette import status
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.core.api.actor_policy import WORKLOAD_NOT_ALLOWED, workload_may_call
from app.core.auth_exemptions import exemption_of
from app.core.live.protocol import CLOSE_FORBIDDEN
from app.modules.test_support.e2e.live_socket import ws_url
from app.modules.test_support.e2e.runtime import backend_server as _backend_server
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

backend_server = _backend_server

_PARAM = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def _routes(app) -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for included in app.routes:
        candidates = getattr(included, "effective_candidates", None)
        for candidate in candidates() if callable(candidates) else [included]:
            route = getattr(candidate, "route", candidate)
            for method in getattr(route, "methods", None) or ():
                if method not in {"HEAD", "OPTIONS"}:
                    found.add((method, route.path))
    return sorted(found)


def _concrete(template: str, values: dict[str, str]) -> str:
    return _PARAM.sub(lambda m: values.get(m.group(1), str(uuid4())), template)


async def _pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "organization_id": org_id,
            "name": f"Conformance {uuid4().hex[:8]}",
            "description": "authorization conformance e2e",
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _workload_headers(
    *, user_id: str, pod_id: str, default_agent: bool
) -> dict[str, str]:
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=DEFAULT_POD_AGENT_ID if default_agent else uuid7(),
        workload_name=DEFAULT_POD_AGENT_NAME if default_agent else "helper",
        pod_id=UUID(pod_id),
        session_id=f"conformance-{uuid4().hex}",
        invoked_by_user_id=UUID(user_id),
    )
    token = await get_user_token(UUID(user_id), delegation_claims=claims)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def world(authenticated_client: AsyncClient, fixed_test_org, fixed_test_user):
    org_id = fixed_test_org["id"]
    pod_id = await _pod(authenticated_client, org_id)
    other_pod_id = await _pod(authenticated_client, org_id)
    workloads = {
        "assistant": await _workload_headers(
            user_id=fixed_test_user["id"], pod_id=pod_id, default_agent=True
        ),
        "agent": await _workload_headers(
            user_id=fixed_test_user["id"], pod_id=pod_id, default_agent=False
        ),
    }
    return {
        "org_id": org_id,
        "pod_id": pod_id,
        "other_pod_id": other_pod_id,
        "workloads": workloads,
    }


def _closed_routes(app, values: dict[str, str]) -> list[tuple[str, str, str]]:
    closed = []
    for method, template in _routes(app):
        if workload_may_call(method, template):
            continue
        path = _concrete(template, values)
        if exemption_of(path, method) is not None:
            continue
        closed.append((method, template, path))
    return closed


@pytest.mark.parametrize("workload", ["assistant", "agent"])
async def test_C2_H1_M2_every_route_a_workload_is_not_opened_to_refuses_it(
    async_client: AsyncClient, test_app, world, workload: str
):
    """C2, C3, H1, H8 (HTTP half), M2, M24: a workload's token was the person on
    every route that built no context; three of them minted sessions."""
    headers = world["workloads"][workload]
    values = {"organization_id": world["org_id"], "pod_id": world["pod_id"]}
    closed = _closed_routes(test_app, values)
    assert len(closed) > 50, "the route enumeration found too few routes"

    wrong = []
    for method, template, path in closed:
        response = await async_client.request(
            method, path, headers=headers, json={} if method != "GET" else None
        )
        code = (
            response.json().get("code")
            if response.headers.get("content-type", "").startswith("application/json")
            and isinstance(response.json(), dict)
            else None
        )
        if response.status_code != status.HTTP_403_FORBIDDEN or (
            code != WORKLOAD_NOT_ALLOWED
        ):
            wrong.append(f"{method} {template} -> {response.status_code} {code}")
    assert not wrong, "\n".join(wrong)


async def test_a_person_is_never_refused_as_a_workload(
    authenticated_client: AsyncClient, test_app, world
):
    """The policy keys on the token's claims, never on the route alone."""
    # Reads only, and an organization that does not exist: nothing is changed.
    values = {"organization_id": str(uuid4()), "pod_id": world["pod_id"]}
    wrong = []
    for method, template, path in _closed_routes(test_app, values):
        if method != "GET":
            continue
        try:
            response = await authenticated_client.get(path)
        except RuntimeError:
            # A route whose dependencies this environment lacks (a sandbox
            # provider) fails past the policy -- which is all this asks.
            continue
        if WORKLOAD_NOT_ALLOWED in response.text:
            wrong.append(f"{method} {template}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("workload", ["assistant", "agent"])
async def test_a_workload_still_reaches_what_it_is_opened_to(
    async_client: AsyncClient, world, workload: str
):
    headers = world["workloads"][workload]

    me = await async_client.get("/users/me", headers=headers)
    own_pod = await async_client.get(f"/pods/{world['pod_id']}/agents", headers=headers)

    assert me.status_code == status.HTTP_200_OK, me.text
    # Past the policy, the pod's own authorization decides: the assistant reads
    # what its person reads, and a named agent with no grants is refused for
    # want of one -- not for being a workload.
    if workload == "assistant":
        assert own_pod.status_code == status.HTTP_200_OK, own_pod.text
    else:
        assert own_pod.json().get("code") == "MISSING_WORKLOAD_RESOURCE_GRANT", (
            own_pod.text
        )


@pytest.mark.parametrize("workload", ["assistant", "agent"])
async def test_a_workload_is_pinned_to_its_own_pod(
    async_client: AsyncClient, world, workload: str
):
    response = await async_client.get(
        f"/pods/{world['other_pod_id']}/agents", headers=world["workloads"][workload]
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text


@pytest.mark.parametrize("workload", ["assistant", "agent"])
@pytest.mark.parametrize(
    ("method", "template"),
    [
        ("POST", "/pods/{pod_id}/join-requests/{join_request_id}/approve"),
        ("PUT", "/pods/{pod_id}/functions/{function_name}/contacts"),
    ],
)
async def test_pod_routes_that_need_a_person_refuse_a_workload(
    async_client: AsyncClient, world, workload: str, method: str, template: str
):
    path = _concrete(template, {"pod_id": world["pod_id"], "function_name": "fn"})

    response = await async_client.request(
        method, path, headers=world["workloads"][workload], json={}
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
    assert response.json()["code"] == WORKLOAD_NOT_ALLOWED


async def _socket_outcome(server, pod_id: str, headers: dict[str, str]) -> object:
    """``"ready"`` if the pod's live socket opens, else the code it closed with."""
    async with connect(
        ws_url(server, f"/pods/{pod_id}/live"), additional_headers=headers
    ) as ws:
        try:
            frame = json.loads(await asyncio.wait_for(ws.recv(), 15))
        except ConnectionClosed as closed:
            return closed.rcvd.code if closed.rcvd else None
        return frame.get("type")


async def test_H8_the_assistant_keeps_the_live_socket_in_its_own_pod(
    backend_server, world
):
    outcome = await _socket_outcome(
        backend_server, world["pod_id"], world["workloads"]["assistant"]
    )

    assert outcome == "ready"


@pytest.mark.parametrize("workload", ["assistant", "agent"])
async def test_H8_a_workload_cannot_open_another_pods_live_socket(
    backend_server, world, workload: str
):
    """H8: the socket kept only the token's user id, so a pod-A agent streamed
    the person's direct messages and inbox in pod B."""
    outcome = await _socket_outcome(
        backend_server, world["other_pod_id"], world["workloads"][workload]
    )

    assert outcome == CLOSE_FORBIDDEN


async def test_H8_a_named_agent_cannot_hold_a_live_socket(backend_server, world):
    """Its topics read as the person; a named agent's grants never applied."""
    outcome = await _socket_outcome(
        backend_server, world["pod_id"], world["workloads"]["agent"]
    )

    assert outcome == CLOSE_FORBIDDEN
