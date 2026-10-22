from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from lemma_sdk.errors import (
    LemmaConfigError,
    LemmaConflictError,
    LemmaNotFoundError,
    LemmaPermissionError,
)
from lemma_sdk.openapi_client.api.connectors import connector_account_create
from lemma_sdk.openapi_client.models.account_create_schema import AccountCreateSchema
from lemma_sdk.resources.connectors import BoundConnectors
from lemma_sdk.transport import LemmaTransport


ORG_ID = "00000000-0000-0000-0000-000000000001"
AUTH_CONFIG_ID = "00000000-0000-0000-0000-000000000002"


def _request(**values: Any) -> AccountCreateSchema:
    return AccountCreateSchema.from_dict({"credentials": {}, **values})


def _resource(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]
) -> BoundConnectors:
    def sync_detailed(organization_id, *, client, body):  # type: ignore[no-untyped-def]
        captured["organization_id"] = organization_id
        captured["body"] = body.to_dict()
        return type(
            "Response",
            (),
            {"status_code": 200, "parsed": {"id": "account-1"}, "headers": {}},
        )()

    monkeypatch.setattr(connector_account_create, "sync_detailed", sync_detailed)
    transport = LemmaTransport(base_url="https://api.example.test", token="test")
    return BoundConnectors(transport, org_id=ORG_ID)


def test_account_create_legacy_name_is_merged_into_body_without_extra_path_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    resource = _resource(monkeypatch, captured)

    result = resource.accounts.create("workspace-slack", _request())

    assert result == {"id": "account-1"}
    assert str(captured["organization_id"]) == ORG_ID
    assert captured["body"]["auth_config_name"] == "workspace-slack"
    assert "auth_config_id" not in captured["body"]


def test_account_create_request_name_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    resource = _resource(monkeypatch, captured)

    resource.accounts.create("legacy-name", _request(auth_config_name="request-name"))

    assert captured["body"]["auth_config_name"] == "request-name"


def test_account_create_preserves_auth_config_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    resource = _resource(monkeypatch, captured)

    resource.accounts.create("ignored", _request(auth_config_id=AUTH_CONFIG_ID))

    assert captured["body"]["auth_config_id"] == AUTH_CONFIG_ID
    assert "auth_config_name" not in captured["body"]


def test_account_create_rejects_conflicting_selectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = _resource(monkeypatch, {})

    with pytest.raises(ValueError, match="Specify only one"):
        resource.accounts.create(
            "legacy-name",
            _request(auth_config_name="request-name", auth_config_id=AUTH_CONFIG_ID),
        )


def test_account_create_rejects_missing_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = _resource(monkeypatch, {})

    with pytest.raises(ValueError, match="Either auth_config_name or auth_config_id"):
        resource.accounts.create("", _request())


def _connectors_over(handler: Any) -> BoundConnectors:
    """A connectors facade whose raw-httpx calls hit a mocked transport."""
    transport = LemmaTransport(base_url="https://api.example.test", token="test")
    transport.generated.set_httpx_client(
        httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.example.test",
        )
    )
    return BoundConnectors(transport, org_id=ORG_ID)


def test_missing_skill_doc_raises_the_same_not_found_as_every_other_call() -> None:
    # These two methods bypass the generated client, and used to raise a bare
    # LemmaAPIError that `except LemmaNotFoundError` did not catch -- dropping
    # the server's code, details and request id with it.
    connectors = _connectors_over(
        lambda _: httpx.Response(
            404,
            json={"message": "no skill doc", "code": "not_found"},
            headers={"x-request-id": "req-9"},
        )
    )

    with pytest.raises(LemmaNotFoundError) as excinfo:
        connectors.apps.skill("github")

    assert excinfo.value.code == "not_found"
    assert excinfo.value.request_id == "req-9"
    assert excinfo.value.message == "no skill doc"


def test_connector_status_maps_a_permission_denial_to_its_typed_error() -> None:
    connectors = _connectors_over(
        lambda _: httpx.Response(403, json={"message": "not your org"})
    )

    with pytest.raises(LemmaPermissionError) as excinfo:
        connectors.status()

    assert excinfo.value.message == "not your org"


POD_ID = "00000000-0000-0000-0000-0000000000aa"
POD_ACCOUNT_ID = "00000000-0000-0000-0000-0000000000bb"
ACCOUNT_ID = "00000000-0000-0000-0000-0000000000cc"


def _pod_connectors_over(handler: Any) -> BoundConnectors:
    transport = LemmaTransport(base_url="https://api.example.test", token="test")
    transport.generated.set_httpx_client(
        httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.example.test",
        )
    )
    return BoundConnectors(transport, org_id=ORG_ID, pod_id=POD_ID)


def test_execute_runs_through_the_pod_connector_named_by_slug() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"sent": True}})

    connectors = _pod_connectors_over(handler)

    result = connectors.execute("support-inbox", "GMAIL_SEND_EMAIL", {"to": "a@b.c"})

    assert result.result == {"sent": True}
    [request] = seen
    assert request.method == "POST"
    assert request.url.path == (
        f"/pods/{POD_ID}/connectors/support-inbox/operations/GMAIL_SEND_EMAIL/execute"
    )
    # The pod connector decides the account: nothing names one.
    assert json.loads(request.content) == {"payload": {"to": "a@b.c"}}


def test_execute_takes_no_account() -> None:
    connectors = _pod_connectors_over(lambda _: httpx.Response(200, json={}))

    with pytest.raises(TypeError):
        connectors.execute("gmail", "op", {}, account_id="x")  # type: ignore[call-arg]


def test_execute_needs_a_pod() -> None:
    transport = LemmaTransport(base_url="https://api.example.test", token="test")
    connectors = BoundConnectors(transport, org_id=ORG_ID)

    with pytest.raises(LemmaConfigError):
        connectors.execute("gmail", "op", {})


def test_a_connection_required_refusal_carries_its_connect_link() -> None:
    link = f"https://app.example.test/pods/{POD_ID}/connections?connector=gmail"
    connectors = _pod_connectors_over(
        lambda _: httpx.Response(
            409,
            json={
                "code": "CONNECTION_REQUIRED",
                "message": "Connect your gmail account to use 'gmail' in this pod.",
                "details": {"pod_connector": "gmail", "connect_url": link},
            },
        )
    )

    with pytest.raises(LemmaConflictError) as excinfo:
        connectors.execute("gmail", "GMAIL_SEND_EMAIL", {})

    assert excinfo.value.code == "CONNECTION_REQUIRED"
    assert excinfo.value.connect_url == link
    assert link in str(excinfo.value)


def test_pod_connector_listing_and_discovery_are_pod_scoped() -> None:
    seen: list[httpx.Request] = []

    discovered = {
        "connector_id": "gmail",
        "items": [],
        "total_operations": 0,
        "returned_count": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/operations"):
            return httpx.Response(200, json=discovered)
        if "/operations/" in request.url.path:
            return httpx.Response(200, json={"name": "op", "input_schema": {}})
        return httpx.Response(200, json={"items": []})

    connectors = _pod_connectors_over(handler)

    connectors.pod_connectors()
    connectors.pod_operations("gmail", "send email", limit=5)
    connectors.pod_operation("gmail", "GMAIL_SEND_EMAIL")

    assert [r.url.path for r in seen] == [
        f"/pods/{POD_ID}/connectors",
        f"/pods/{POD_ID}/connectors/gmail/operations",
        f"/pods/{POD_ID}/connectors/gmail/operations/GMAIL_SEND_EMAIL",
    ]
    assert dict(seen[1].url.params) == {"query": "send email", "limit": "5"}


def test_declaring_a_connector_and_sharing_an_account_are_pod_requests() -> None:
    seen: list[httpx.Request] = []

    stamp = "2026-10-22T00:00:00Z"
    connector = {
        "id": POD_ACCOUNT_ID,
        "pod_id": POD_ID,
        "slug": "support-inbox",
        "auth_config_id": AUTH_CONFIG_ID,
        "mode": "SHARED",
        "required": False,
        "visibility": "POD",
        "status": "READY",
        "created_at": stamp,
        "updated_at": stamp,
    }
    pod_account = {
        "id": POD_ACCOUNT_ID,
        "pod_id": POD_ID,
        "account_id": ACCOUNT_ID,
        "auth_config_id": AUTH_CONFIG_ID,
        "owner_user_id": ACCOUNT_ID,
        "visibility": "POD",
        "is_member_default": False,
        "source": "MANUAL",
        "created_at": stamp,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"items": []})
        if request.url.path.endswith("/connectors"):
            return httpx.Response(201, json=connector)
        return httpx.Response(201, json=pod_account)

    connectors = _pod_connectors_over(handler)

    connectors.declare_pod_connector(
        "support-inbox",
        AUTH_CONFIG_ID,
        "SHARED",
        shared_pod_account_id=POD_ACCOUNT_ID,
        purpose="The team inbox",
    )
    connectors.add_pod_account(ACCOUNT_ID, visibility="POD")
    connectors.pod_accounts()

    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", f"/pods/{POD_ID}/connectors"),
        ("POST", f"/pods/{POD_ID}/connector-accounts"),
        ("GET", f"/pods/{POD_ID}/connector-accounts"),
    ]
    assert json.loads(seen[0].content) == {
        "slug": "support-inbox",
        "auth_config_id": AUTH_CONFIG_ID,
        "mode": "SHARED",
        "shared_pod_account_id": POD_ACCOUNT_ID,
        "required": False,
        "purpose": "The team inbox",
    }
    assert json.loads(seen[1].content) == {
        "account_id": ACCOUNT_ID,
        "visibility": "POD",
    }
