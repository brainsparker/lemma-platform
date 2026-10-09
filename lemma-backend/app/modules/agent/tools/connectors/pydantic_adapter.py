"""Connector toolset: call an installed connector's operations directly.

Agents previously reached connectors only by shelling out to `lemma connectors
operations ...` inside the sandbox. That works, and still does, but it costs a
sandbox round trip per call and is unavailable to any agent without the
workspace toolset.

The loop is search, describe, run: an org can have thousands of operations once
a couple of MCP servers are installed, so operations are found rather than
enumerated, and their schemas are fetched on demand rather than compiled into
per-operation tools.

Arguments are validated here against the operation's own stored schema, and a
mismatch comes back as a structured result rather than an exception, so the
model can correct itself instead of the run failing.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import jsonschema
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.tool_payload_limits import bounded_tool_payload
from app.modules.agent.tools.tool_errors import safe_error_text
from app.core.domain.errors import DomainError
from app.core.redaction import redact_text
from app.modules.agent.domain.value_objects import to_json_value
from app.modules.agent.tools.connectors.connector_access import (
    connector_execution_only,
    connector_services,
    find_file_result,
)
from app.modules.agent.tools.connectors.models import (
    DescribeConnectorOperationRequest,
    RunConnectorOperationRequest,
    SearchConnectorOperationsRequest,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.connectors.services.connector_operation_search import (
    search_across_auth_configs,
)

_MAX_VIOLATIONS = 10
# Installs listed in one call. An org past it is told so rather than shown a
# list that silently stops.
_LIST_LIMIT = 200

#: What a connector error's details may carry back to the model. Connector
#: errors already pass theirs through an allowlist and redaction; this is the
#: same short list again, so a domain error from elsewhere cannot widen it.
_DETAIL_KEYS = (
    "operation_name",
    "upstream_message",
    "upstream_status",
    "server_host",
    "reason",
    "retry_after",
)


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """A failure the model can act on, rather than one that ends the run.

    `success: False` is the uniform contract `result_is_failure` reads. Without
    it every connector failure counted as a successful tool call -- in spans,
    and in the `isError` an MCP client sees.
    """
    return {"success": False, "error": message, "code": code, **extra}


def _failure(
    exc: DomainError,
    *,
    auth_config: str | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    """A connector error, naming what failed and what the provider said.

    The error types fix their own message ("provider is temporarily
    unavailable"), so the text alone named neither the install, the operation,
    the server nor anything the server had said -- the parts a model needs to
    fix its call or tell the person which connector is down.
    """
    details = exc.details if isinstance(exc.details, dict) else {}
    carried = {
        key: redact_text(value) if isinstance(value, str) else value
        for key in _DETAIL_KEYS
        if (value := details.get(key)) is not None
    }
    named = {
        key: value
        for key, value in (("auth_config", auth_config), ("operation", operation))
        if value
    }
    return _error(
        exc.code or "connector_error", safe_error_text(exc), **named, **carried
    )


def _no_organization() -> dict[str, Any]:
    return _error(
        "no_organization",
        "This conversation is not bound to an organization, so connectors are "
        "unavailable.",
    )


connectors_toolset = FunctionToolset()


@connectors_toolset.tool
async def list_connectors(ctx: RunContext[BaseAgentContext]) -> dict[str, Any]:
    """List the connectors installed in this organization that you may use.

    Returns each install's `auth_config` name, which is how every other
    connector tool addresses it.
    """
    deps = ctx.deps
    if deps.org_id is None:
        return _no_organization()

    async with connector_services(deps) as services:
        try:
            configs, next_cursor = await services.connector.list_auth_configs(
                user_id=deps.user_id, organization_id=deps.org_id, limit=_LIST_LIMIT
            )
        except DomainError as exc:
            # The other three tools already return failures as data. Raising
            # here instead would end the agent's run over something it could
            # simply report -- an org it cannot read is not a crash.
            return _failure(exc)
        items = [
            {
                "auth_config": config.name,
                "connector_id": config.connector_id,
                "kind": config.kind.value,
                "status": config.status.value
                if hasattr(config.status, "value")
                else str(config.status),
                "is_default": config.is_default,
            }
            for config in configs
        ]
    if next_cursor is None:
        return {"items": items, "count": len(items)}
    return {
        "items": items,
        "count": len(items),
        "truncated": True,
        "note": (
            f"Only the first {_LIST_LIMIT} installs are listed. Use "
            "`search_connector_operations` without `auth_config` to search "
            "every install for what you need."
        ),
    }


@connectors_toolset.tool
async def search_connector_operations(
    ctx: RunContext[BaseAgentContext], request: SearchConnectorOperationsRequest
) -> dict[str, Any]:
    """Find operations by what they do, ranked by relevance.

    Start here rather than guessing an operation name: an install can expose
    hundreds of operations, and the names are provider-specific. Leave
    `auth_config` unset to search every installed connector at once -- each hit
    names the `auth_config` to run it against, so you do not need to know which
    install provides a capability before searching for it.
    """
    deps = ctx.deps
    if deps.org_id is None:
        return _no_organization()

    async with connector_services(deps) as services:
        try:
            if request.auth_config:
                found = await services.operations.discover_operations_for_auth_config(
                    user_id=deps.user_id,
                    organization_id=deps.org_id,
                    auth_config_name=request.auth_config,
                    query=request.query,
                    limit=request.limit,
                )
            else:
                found = await search_across_auth_configs(
                    services.operations,
                    user_id=deps.user_id,
                    organization_id=deps.org_id,
                    query=request.query,
                    limit=request.limit,
                )
        except DomainError as exc:
            return _failure(exc, auth_config=request.auth_config)
    return to_json_value(found)


@connectors_toolset.tool
async def describe_connector_operation(
    ctx: RunContext[BaseAgentContext], request: DescribeConnectorOperationRequest
) -> dict[str, Any]:
    """Get one operation's description and input/output schemas.

    Call this before running an operation you have not run before -- the input
    schema is what `run_connector_operation` validates `arguments` against.
    """
    deps = ctx.deps
    if deps.org_id is None:
        return _no_organization()

    async with connector_services(deps) as services:
        try:
            detail = await services.operations.get_operation_details_for_auth_config(
                user_id=deps.user_id,
                organization_id=deps.org_id,
                auth_config_name=request.auth_config,
                operation_name=request.operation,
            )
        except DomainError as exc:
            return _failure(
                exc, auth_config=request.auth_config, operation=request.operation
            )
    # Whole input and output schemas; some connectors publish very large ones.
    return bounded_tool_payload(to_json_value(detail), what="operation schema")


def _validate_arguments(
    schema: dict[str, Any] | None, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """Return a structured error if the arguments do not fit, else None."""
    if not schema:
        return None
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(arguments), key=lambda e: list(e.absolute_path)
    )
    if not errors:
        return None
    return _error(
        "invalid_arguments",
        "The arguments do not match this operation's input schema.",
        violations=[
            {
                "path": "/".join(str(part) for part in error.absolute_path) or "(root)",
                "message": error.message,
            }
            for error in errors[:_MAX_VIOLATIONS]
        ],
        # Handed back so the model can correct itself without another round trip.
        input_schema=schema,
    )


@connectors_toolset.tool
async def run_connector_operation(
    ctx: RunContext[BaseAgentContext], request: RunConnectorOperationRequest
) -> dict[str, Any]:
    """Run an operation on an installed connector.

    `arguments` must match the operation's input schema -- fetch it with
    `describe_connector_operation` first. To send a file (an email attachment,
    a Drive upload), pass it as `{"pod_path": "/me/report.pdf"}` in the file
    argument; a file in your sandbox has to be uploaded to the pod first. A
    file result larger than the inline limit is written to the pod datastore
    and returned as a reference; pass `output_path` to choose where it lands.
    """
    deps = ctx.deps
    if deps.org_id is None:
        return _no_organization()

    account_id: UUID | None = None
    if request.account_id:
        try:
            account_id = UUID(request.account_id)
        except ValueError:
            return _error("invalid_account_id", "account_id must be a UUID.")

    try:
        # Phase 1, in a short scope: every DB read, the authorization check and
        # the credential resolution. The scope closes -- releasing the pooled
        # connection -- before anything reaches the provider.
        async with connector_services(deps) as services:
            detail = await services.operations.get_operation_details_for_auth_config(
                user_id=deps.user_id,
                organization_id=deps.org_id,
                auth_config_name=request.auth_config,
                operation_name=request.operation,
            )
            invalid = _validate_arguments(
                getattr(detail, "input_schema", None), dict(request.arguments or {})
            )
            if invalid is not None:
                return invalid

            # `output_path` is Lemma's argument and is kept out of the payload:
            # it used to be sent to the provider along with everything else.
            payload = dict(request.arguments or {})

            resolved = await services.operations.resolve_execution_for_auth_config(
                user_id=deps.user_id,
                organization_id=deps.org_id,
                auth_config_name=request.auth_config,
                operation_name=request.operation,
                payload=payload,
                actor=services.ctx,
                account_id=account_id,
                # An agent acts as the app where the provider allows it, so a
                # schedule keeps working after the person who set it up leaves.
                # Everything else -- pod publish and import in particular --
                # says nothing here and stays the person.
                act_as="app",
            )
            # The files the arguments name, read as this agent -- its delegated
            # grants, not the account owner's -- so a connector call cannot send
            # anything the agent could not have read itself.
            resolved = await services.files.prepare(resolved)

        # Phase 2: the provider call, holding no connection. The REST path has
        # split these two since it was written; this one had not, so the
        # hottest connector path in the system was the one that pinned a
        # connection for the length of an external call.
        async with connector_execution_only() as operations:
            response = await operations.execute_resolved(resolved)
        # Phase 3: a file result lands in the pod when it is large or asked
        # for. The REST route has always done this; this path skipped it, so a
        # download came back as an inline blob however big it was.
        found = await find_file_result(response)
        if found is not None:
            async with connector_services(deps) as services:
                response = await services.files.capture(
                    response,
                    found,
                    connector_id=resolved.connector_id,
                    output_path=request.output_path,
                )
    except DomainError as exc:
        # Connector failures are information for the model (wrong argument,
        # account needs reconnecting), not a reason to end the run.
        return _failure(
            exc, auth_config=request.auth_config, operation=request.operation
        )
    return bounded_tool_payload(to_json_value(response), what="connector response")
