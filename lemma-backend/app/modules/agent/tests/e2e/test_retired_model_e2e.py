"""A retired or unknown model is refused when it is named, not swapped at run.

A teammate pinned to a model its provider no longer offers used to run -- and
be billed -- on the provider's default instead, with a log line as the only
trace; and nothing checked a runtime when it was saved. Over HTTP, against the
real repositories: a save naming an unknown model answers 422, and a turn on a
model removed since answers 422 before any run exists.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from starlette import status

from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod

pytestmark = pytest.mark.e2e

# Nothing listens on the discard port, so discovery finds no models and the
# names given to the profile are its whole catalog.
_NOTHING_LISTENING = "http://127.0.0.1:9/v1"


async def _provider(
    client: AsyncClient, org_id: str, *, model_names: list[str]
) -> dict:
    response = await client.post(
        f"/organizations/{org_id}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Provider {uuid4().hex[:8]}",
            "base_url": _NOTHING_LISTENING,
            "model_names": model_names,
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


@pytest.mark.asyncio
async def test_saving_a_teammate_on_a_model_the_provider_does_not_offer_is_refused(
    authenticated_client: AsyncClient, fixed_test_org: dict
) -> None:
    org_id = fixed_test_org["id"]
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    provider = await _provider(authenticated_client, org_id, model_names=["kept"])

    unknown = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"Pinned {uuid4().hex[:6]}",
            "instruction": "Answer.",
            "agent_runtime": {"profile_id": provider["id"], "model_name": "never"},
        },
    )
    assert unknown.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, unknown.text
    assert unknown.json()["code"] == "model_unavailable"

    missing_profile = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_runtime": {"profile_id": str(uuid4())}},
    )
    assert missing_profile.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert missing_profile.json()["code"] == "runtime_profile_unavailable"


@pytest.mark.asyncio
async def test_a_turn_on_a_model_removed_since_is_refused_before_a_run_exists(
    authenticated_client: AsyncClient, fixed_test_org: dict
) -> None:
    org_id = fixed_test_org["id"]
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    provider = await _provider(
        authenticated_client, org_id, model_names=["kept", "retiring"]
    )
    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_runtime": {"profile_id": provider["id"], "model_name": "retiring"}
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    retired = await authenticated_client.patch(
        f"/organizations/{org_id}/agent-runtime/profiles/{provider['id']}",
        json={"source": "OPENAI_COMPATIBLE", "model_names": ["kept"]},
    )
    assert retired.status_code == status.HTTP_200_OK, retired.text

    sent = await authenticated_client.post(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        json={"content": "hello"},
    )
    assert sent.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, sent.text
    assert sent.json()["code"] == "model_unavailable"
    assert "retiring" in sent.json()["message"]

    detail = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}"
    )
    assert detail.status_code == status.HTTP_200_OK, detail.text
    assert detail.json()["last_run_status"] is None
