"""What removing an installation would stop, shown before anyone removes it.

Design §3.6: "Impact preview first (R8): pods, pod connectors, pod accounts,
surfaces, schedules, subscriptions it will stop." The counts are the whole
organization's -- the person about to remove it is removing it for everyone --
and the pods are named only where that person could open them: an org editor
who is not in a pod learns that something there stops, not what the pod is
called.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from starlette import status

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import AgentSurfaceStatus
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.mcp_event import (
    ConnectorEventSubscription,
)
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.pod.infrastructure.models.pod_models import Pod
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
)

pytestmark = pytest.mark.e2e


async def _install(scenario: E2EScenario, db_session) -> AuthConfig:
    connector_id = f"crm-{uuid4().hex[:8]}"
    db_session.add(
        Connector(
            id=connector_id,
            title="CRM",
            description="impact e2e",
            kinds=[{"kind": "http", "auth_scheme": "API_KEY"}],
            is_active=True,
        )
    )
    await db_session.flush()
    install = AuthConfig(
        organization_id=UUID(scenario.org_id),
        connector_id=connector_id,
        kind="http",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
        name=connector_id,
    )
    db_session.add(install)
    await db_session.commit()
    return install


async def _declare(scenario: E2EScenario, pod_id: str, install: AuthConfig, mode: str):
    made = await scenario.owner_client.post(
        f"/pods/{pod_id}/connectors",
        json={"slug": "crm", "auth_config_id": str(install.id), "mode": mode},
    )
    assert made.status_code == status.HTTP_201_CREATED, made.text


async def _surface(
    db_session, pod_id: str, slug: str, agent_id: str, platform: str = "SLACK"
) -> None:
    pod = UUID(pod_id)
    db_session.add(
        AgentSurface(
            pod_id=pod,
            organization_id=await db_session.scalar(
                select(Pod.organization_id).where(Pod.id == pod)
            ),
            name=f"{platform.lower()}-{uuid4().hex[:8]}",
            agent_id=UUID(agent_id),
            surface_type=platform,
            event_mode="WEBHOOK",
            credential_mode="CUSTOM",
            config={},
            status=AgentSurfaceStatus.ACTIVE.value,
            pod_connector_slug=slug,
        )
    )
    await db_session.commit()


async def _schedule(db_session, pod_id: str, owner_id: str, **config) -> None:
    db_session.add(
        Schedule(
            user_id=UUID(owner_id),
            pod_id=UUID(pod_id),
            name=f"listen-{uuid4().hex[:8]}",
            schedule_type="WEBHOOK",
            pod_connector_slug="crm",
            config=config,
        )
    )
    await db_session.commit()


async def _subscription(db_session, install: AuthConfig, owner_id: str) -> str:
    account = await AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    ).create(
        AccountEntity(
            user_id=UUID(owner_id),
            organization_id=install.organization_id,
            auth_config_id=install.id,
            connector_id=install.connector_id,
            is_default=True,
            credentials={"api_key": "owner-key"},
        )
    )
    row = ConnectorEventSubscription(
        organization_id=install.organization_id,
        auth_config_id=install.id,
        account_id=account.id,
        user_id=UUID(owner_id),
        name="ticket.created",
        arguments={},
        secret_ciphertext="sealed",
    )
    db_session.add(row)
    await db_session.commit()
    return str(row.id)


async def test_the_preview_counts_surfaces_schedules_and_subscriptions_by_pod(
    scenario: E2EScenario, db_session
):
    await scenario.create_org_with_pod(name_prefix="Impact Hidden")
    hidden = scenario.pod
    await scenario.create_pod(name_prefix="Impact Shared")
    shared = scenario.pod
    install = await _install(scenario, db_session)
    owner_id = scenario.owner_user["id"]
    await _declare(scenario, hidden["id"], install, "PER_MEMBER")
    await _declare(scenario, shared["id"], install, "SHARED")
    agent = await scenario.create_agent()
    await _surface(db_session, shared["id"], "crm", agent["id"])
    await _surface(db_session, shared["id"], "something-else", agent["id"], "TELEGRAM")
    subscription = await _subscription(db_session, install, owner_id)
    await _schedule(
        db_session,
        shared["id"],
        owner_id,
        source="mcp",
        provider_trigger_id=subscription,
    )
    await _schedule(db_session, hidden["id"], owner_id)

    # An org editor who is in the shared pod only.
    editor = await scenario.create_user("impact-editor")
    seat = await invite_org_member(
        scenario.owner_client,
        scenario.async_client,
        org_id=scenario.org_id,
        user=editor,
    )
    promoted = await scenario.owner_client.patch(
        f"/organizations/{scenario.org_id}/members/{seat['id']}/role",
        json={"role": "ORG_EDITOR"},
    )
    assert promoted.status_code == status.HTTP_200_OK, promoted.text
    await add_pod_member(
        scenario.owner_client,
        pod_id=shared["id"],
        organization_member_id=seat["id"],
        role="POD_USER",
    )
    path = f"/organizations/{scenario.org_id}/installations/{install.id}/impact"

    as_owner = await scenario.owner_client.get(path)

    assert as_owner.status_code == status.HTTP_200_OK, as_owner.text
    body = as_owner.json()
    assert (
        body["surface_count"],
        body["schedule_count"],
        body["event_subscription_count"],
    ) == (1, 2, 1)
    pods = {one["pod_id"]: one for one in body["pods"]}
    assert set(pods) == {hidden["id"], shared["id"]}, "an owner opens every pod"
    assert pods[shared["id"]]["name"] == shared["name"]
    assert (
        pods[shared["id"]]["surface_count"],
        pods[shared["id"]]["schedule_count"],
        pods[shared["id"]]["event_subscription_count"],
        pods[shared["id"]]["pod_connectors"],
    ) == (1, 1, 1, ["crm"])
    assert (
        pods[hidden["id"]]["surface_count"],
        pods[hidden["id"]]["schedule_count"],
    ) == (0, 1)

    as_editor = await scenario.async_client.get(path, headers=auth_headers(editor))

    assert as_editor.status_code == status.HTTP_200_OK, as_editor.text
    seen = as_editor.json()
    assert seen["schedule_count"] == 2, "the count is everyone's"
    assert [one["pod_id"] for one in seen["pods"]] == [shared["id"]]
    assert hidden["name"] not in as_editor.text, "a pod they cannot open is not named"
