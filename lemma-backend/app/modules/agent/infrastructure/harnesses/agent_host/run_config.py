"""Reading a dispatched run's settings out of its runtime profile.

Separated from the harness because these are pure translation: what the
profile says becomes what the run spec carries. The harness is about
driving a run once those are settled.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import JsonObject


@dataclass(frozen=True, slots=True)
class AgentHostRunConfig:
    harness_id: UUID
    runtime_profile_id: UUID
    config_selections: JsonObject
    wait_timeout_seconds: int
    model_name: str | None


def agent_host_run_config[DepsT](options: HarnessOptions[DepsT]) -> AgentHostRunConfig:
    snapshot = options.runtime_profile
    if snapshot is None:
        raise ValueError("runtime profile is missing from harness options")
    # Validated here so a profile with no saved revision fails before dispatch,
    # naming the field, rather than as a KeyError. Admission intentionally
    # uses the latest live revision after selections are revalidated by the
    # repository.
    config = snapshot.harness_config()
    # The model comes from the profile snapshot rather than options.model_name,
    # which substitutes a "default" placeholder when nothing is pinned. Agent
    # Host rejects any model the harness does not advertise, so an unpinned
    # profile must send no model and let the harness use its own default.
    raw_model = snapshot.provider_model_name or snapshot.model_name
    model_name = raw_model.strip() if raw_model else None
    return AgentHostRunConfig(
        harness_id=snapshot.harness_uuid(),
        runtime_profile_id=UUID(snapshot.profile_id),
        config_selections=dict(config.config_selections),
        wait_timeout_seconds=config.host_wait_timeout_seconds,
        model_name=model_name or None,
    )


def resolve_pod_cwd(conversation: Conversation) -> str:
    from app.modules.agent.domain.workspace_location import resolve_pod_cwd

    return resolve_pod_cwd(conversation)


def json_object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def joined_prompt(prompt: JsonObject) -> str:
    parts = [
        str(prompt.get(key) or "")
        for key in ("system_prompt", "recovery_system_prompt", "user_prompt")
    ]
    return "\n\n".join(part for part in parts if part)
