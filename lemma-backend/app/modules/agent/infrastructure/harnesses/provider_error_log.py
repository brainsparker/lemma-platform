"""Server-side diagnostics for provider HTTP failures.

Production logged only the status code, which made these undiagnosable: a week
of 400s could be a context overflow, a malformed tool schema, or an image sent
to a text-only model, and nothing distinguished them.

The obvious fix — log the response body — is forbidden here, and rightly: the
logging contract (`app/core/log/log.py::_PROHIBITED_FIELDS`) bans `body`,
`message`, `headers` and friends because provider errors echo the request, keys
included. So instead of the body we extract the two short, enum-like identifiers
every provider puts beside the prose:

    OpenAI-compatible: {"error": {"type": "invalid_request_error",
                                  "code": "context_length_exceeded", ...}}
    Anthropic:         {"type": "error", "error": {"type": "invalid_request_error"}}

Those are the fields that actually separate one 400 from another, and they carry
no user or credential data. Anything that isn't a short identifier is dropped
rather than truncated — a value long enough to be prose is prose.
"""

from __future__ import annotations

from pydantic_ai.exceptions import ModelHTTPError

from app.core.log.log import get_logger
from app.modules.agent.infrastructure.harnesses.run_failure_classification import (
    classify_run_failure,
    provider_error_identifiers as provider_error_identifiers,
)

logger = get_logger(__name__)


def log_model_http_error(
    exc: ModelHTTPError,
    *,
    agent_run_id: object,
    model_name: str | None = None,
) -> None:
    """Record everything the provider told us about why the request failed.

    The `provider_error_*` fields are the enum-like identifiers that make these
    groupable (`context_length_exceeded` vs `invalid_request_error`); the
    exception message and traceback come from `exc_info` and carry the prose the
    provider actually returned.
    """
    kind, code = provider_error_identifiers(getattr(exc, "body", None))
    logger.error(
        "agent.pydantic_ai.model_request_status_model.failed",
        status_code=exc.status_code,
        model_name=model_name or getattr(exc, "model_name", None),
        agent_run_id=str(agent_run_id),
        provider_error_kind=kind,
        provider_error_code=code,
        exc_info=exc,
    )


def user_facing_error_message(exc: BaseException) -> str:
    """A sanitized, actionable message for the UI: the runner's own sentence.

    Never raw provider exception text, which may contain API keys, request
    headers, or model-internal details.
    """
    return classify_run_failure(exc).message
