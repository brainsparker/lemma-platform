"""What any member of an organization may read of a provider profile.

The listing needs only membership. It used to redact by guessing secret key
names, which passed a key in the base URL's query, a password in its userinfo
and any header not named like a credential, while masking `max_tokens` -- a
mask an editor then saved back, breaking every run on the profile.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    AzureOpenAIRuntimeConfig,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    public_url,
)
from app.modules.agent.domain.sentinels import UNSET
from app.modules.agent.services.runtime_provider_patch import resolve_provider_patch

pytestmark = pytest.mark.unit


def _profile(config: OpenAICompatibleRuntimeConfig | AzureOpenAIRuntimeConfig):
    protocol = (
        RuntimeProfileProtocol.AZURE_OPENAI
        if isinstance(config, AzureOpenAIRuntimeConfig)
        else RuntimeProfileProtocol.OPENAI_COMPATIBLE
    )
    return AgentRuntimeProfile(
        id=str(uuid4()),
        organization_id=uuid4(),
        scope=RuntimeProfileScope.ORGANIZATION,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=protocol,
        name="Gateway",
        default_model_name="model-a",
        model_catalog=[
            RuntimeModelCatalogEntry(name="model-a", provider_model_name="model-a")
        ],
        config=config,
    )


def test_the_listing_strips_userinfo_and_query_from_the_base_url() -> None:
    profile = _profile(
        OpenAICompatibleRuntimeConfig(
            base_url="https://user:pw@proxy.example.com:8443/v1?key=abc#frag"
        )
    )

    config = profile.public_dict()["config"]

    assert config["base_url"] == "https://proxy.example.com:8443/v1"
    assert "pw" not in str(config) and "abc" not in str(config)


def test_the_listing_strips_secrets_from_an_azure_endpoint() -> None:
    profile = _profile(
        AzureOpenAIRuntimeConfig(
            azure_endpoint="https://admin:hunter2@res.example.com/?sig=xyz",
            azure_version="2024-10-21",
            deployment_id="prod",
        )
    )

    config = profile.public_dict()["config"]

    assert config["azure_endpoint"] == "https://res.example.com/"
    assert (config["azure_version"], config["deployment_id"]) == ("2024-10-21", "prod")


def test_the_listing_keeps_numeric_tuning_rather_than_masking_it() -> None:
    settings = {"max_tokens": 4096, "thinking": {"budget_tokens": 2048}}
    profile = _profile(
        OpenAICompatibleRuntimeConfig(
            base_url="https://api.example.com/v1", model_settings=settings
        )
    )

    assert profile.public_dict()["config"]["model_settings"] == settings


@pytest.mark.parametrize(
    "headers",
    [
        {"Ocp-Apim-Subscription-Key": "SECRET::apim"},
        {"X-Gateway": "SECRET::plain", "HTTP-Referer": "SECRET::referer"},
        {"Authorization": "SECRET::bearer", "x-tenant": "SECRET::tenant"},
    ],
)
def test_the_listing_never_carries_a_header_value(headers: dict[str, str]) -> None:
    profile = _profile(
        OpenAICompatibleRuntimeConfig(
            base_url="https://api.example.com/v1", headers=headers
        )
    )

    public = profile.public_dict()

    assert "SECRET::" not in repr(public)
    assert public["config"]["header_names"] == sorted(headers)


def test_public_url_keeps_an_ipv6_host_bracketed() -> None:
    assert public_url("http://u:p@[::1]:8080/v1?x=1") == "http://[::1]:8080/v1"


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_settings", {"max_tokens": "<redacted>"}),
        ("model_settings", {"thinking": {"budget_tokens": "<redacted>"}}),
        ("headers", {"Authorization": "<redacted>"}),
    ],
)
def test_an_edit_carrying_the_old_mask_is_refused(
    field: str, value: dict[str, object]
) -> None:
    profile = _profile(OpenAICompatibleRuntimeConfig(base_url="https://a.example.com"))
    request: dict[str, object] = {
        "name": UNSET,
        "description": UNSET,
        "base_url": UNSET,
        "api_key": UNSET,
        "model_names": UNSET,
        "headers": UNSET,
        "model_settings": UNSET,
        field: value,
    }

    with pytest.raises(ValueError, match="<redacted>"):
        resolve_provider_patch(
            profile,
            profile.config,
            is_anthropic=False,
            refresh_models=False,
            **request,
        )
