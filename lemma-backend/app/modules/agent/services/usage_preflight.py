"""Refusing to start a run the account has no headroom for.

Every way a run begins has to ask: a new message, the follow-up turn for a
queued one, a retry, and the run that resumes a pause. One that skipped it
would be a free way past a limit the others are held to, so the check lives
here rather than on whichever of them happened to need it first.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.usage.contracts.execution import UsageService


def _scope_allows(scope: object) -> bool:
    """Whether one usage window still has headroom.

    `get_usage_limits` answers with a `dict[str, object]`, so each window
    arrives untyped. Anything unreadable counts as allowed: this list only
    names the limits to report, and the decision to block was already made by
    the `allowed` flag above.
    """
    return not isinstance(scope, dict) or bool(scope.get("allowed", True))


async def assert_usage_preflight_allowed(
    usage_service: UsageService | None,
    *,
    organization_id: UUID | None,
    user_id: UUID,
    agent_runtime: AgentRuntimeConfig,
) -> None:
    """Raise ``UsageLimitExceededError`` when the run must not start."""
    if usage_service is None:
        return
    if not agent_runtime.profile_id.startswith("system:"):
        return
    limits = await usage_service.get_usage_limits(
        organization_id=organization_id,
        user_id=user_id,
    )
    if limits["allowed"]:
        return
    # Say which limit was reached, not just that one was: a person told
    # only "limit exceeded" cannot tell whether to wait for the month to
    # turn or to ask an admin for headroom. See PS-OPS-012.
    reached = [
        name
        for name, key in (
            ("organization monthly", "org_monthly"),
            ("user weekly", "user_weekly"),
            ("user monthly", "user_monthly"),
        )
        if not _scope_allows(limits[key])
    ]
    raise UsageLimitExceededError(
        "LLM usage limit exceeded: "
        + ", ".join(f"{name} limit reached" for name in reached)
    )
