"""What a resolved runtime is, and which catalog model a run gets.

Split from ``runtime_profile_service`` by use case: listing and resolving
profiles lives there; the resolved value and the pure model choice live here,
so both can be read and tested without the repository behind them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from app.core.log.log import get_logger
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
)
from app.modules.agent.domain.errors import RuntimeUnavailableError
from app.modules.agent.domain.value_objects import HarnessKind, JsonObject

logger = get_logger(__name__)


@dataclass(slots=True)
class ResolvedAgentRuntime:
    profile: AgentRuntimeProfile
    harness_kind: HarnessKind
    model: RuntimeModelCatalogEntry | None
    provider_model_name: str | None
    # Revealed plaintext, for the harness alone; kept out of the repr so a
    # logged or raised ResolvedAgentRuntime does not print the key.
    credentials: dict[str, object] | None = field(repr=False)
    # What the runtime can do when no catalog entry is selected. A harness
    # profile routinely pins no model -- the harness picks its own -- and
    # capabilities must survive that, because "nothing selected" says nothing
    # about whether the thing on the other end can read an image.
    unselected_capabilities: list[RuntimeModelCapability] = field(default_factory=list)

    @property
    def capabilities(self) -> list[RuntimeModelCapability]:
        """What this runtime can do, selected model or not.

        Read this rather than `model.capabilities`: an Agent Host profile
        routinely selects no model, and `model is None` says nothing about
        whether the thing on the other end can read an image.
        """
        return list(
            self.model.capabilities if self.model else self.unselected_capabilities
        )

    @property
    def model_name_for_harness(self) -> str:
        if self.model is None:
            return "default"
        return self.provider_model_name or self.model.name

    def runtime_snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            profile_id=self.profile.id,
            profile_name=self.profile.name,
            user_id=str(self.profile.user_id) if self.profile.user_id else None,
            harness_id=str(self.profile.harness_id)
            if self.profile.harness_id
            else None,
            scope=self.profile.scope.value,
            protocol=self.profile.protocol.value,
            model_name=self.model.name if self.model else None,
            provider_model_name=self.provider_model_name,
            model_metadata=dict(self.model.metadata) if self.model else {},
            # Carried so paths that rebuild a context from the snapshot — the
            # MCP bridges, notably — can work out whether this model reads
            # images, instead of assuming it cannot and delegating needlessly.
            # Sourced from the selected model when there is one, and from the
            # runtime itself when there is not. It used to be `[] if not
            # self.model`, which read "this model cannot see" for every Agent
            # Host run -- those pin no model -- so `pod_view_document_pages`
            # refused and `view_image` was withheld from hosts that read images
            # natively.
            model_capabilities=[capability.value for capability in self.capabilities],
            config=_config_dict(self.profile.config),
        )


class RuntimeSnapshot(BaseModel):
    """What a run needs to rebuild its model client, unredacted. Internal.

    It carries the provider's full config, custom headers included, so it is
    never returned from an API or logged; the listing's projection is
    ``AgentRuntimeProfile.public_dict``. Named for what it is: it used to be
    ``public_snapshot``, an invitation to do exactly that.
    """

    model_config = ConfigDict(frozen=True)

    profile_id: str
    profile_name: str
    user_id: str | None = None
    harness_id: str | None = None
    scope: str
    protocol: str
    model_name: str | None = None
    provider_model_name: str | None = None
    model_metadata: JsonObject = Field(default_factory=dict)
    model_capabilities: list[str] = Field(default_factory=list)
    config: JsonObject = Field(default_factory=dict, repr=False)

    def as_payload(self) -> dict[str, object | None]:
        """The dict form the harness options and usage metering still take."""
        return self.model_dump(mode="json")


def selected_model(
    profile: AgentRuntimeProfile,
    requested_model_name: str | None,
) -> RuntimeModelCatalogEntry | None:
    """The catalog entry a run uses, or ``None`` when nothing is pinned.

    Never a substitute. A model the profile no longer offers used to be
    swapped for its default, or its first entry, with only a log line: a
    teammate pinned to one model ran -- and was billed -- on another, with
    different behaviour, indefinitely and invisibly. Now the run is refused
    with the reason, and the person picks the model.

    The one ``None`` that is not a refusal: a coding agent whose catalog is
    empty publishes no list to check against, and an unpinned profile there
    means "the agent's own default".
    """
    model_name = requested_model_name or profile.default_model_name
    if not model_name:
        return None
    for model in profile.model_catalog:
        if model_name == model.name:
            return model
    if profile.harness_id is not None and not profile.model_catalog:
        return None
    logger.warning(
        "agent.runtime_profile.model_unavailable.refused",
        profile_id=profile.id,
        requested_model_name=model_name,
    )
    raise model_unavailable_error(profile.name, model_name)


def model_unavailable_error(
    profile_name: str, model_name: str
) -> RuntimeUnavailableError:
    return RuntimeUnavailableError(
        f"{profile_name} no longer offers {model_name}, the model this teammate "
        "is set to use. Pick another model for the teammate, or refresh the "
        "model list in Settings → Models."
    )


def _config_dict(config: object | None) -> dict[str, object]:
    if config is None:
        return {}
    if isinstance(config, BaseModel):
        return config.model_dump(mode="json")
    if isinstance(config, dict):
        return config
    return {}
