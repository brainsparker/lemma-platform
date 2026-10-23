"""A pod's connectors travel in its bundle, and land in another organization.

The exporting pod declares two connectors on its org's installation of a CRM:
``crm``, per member and required, and ``crm-shared``, running as the owner's
account they shared with the pod. An agent is granted ``connector.use`` on
``crm-shared`` -- by slug. The bundle carries both connectors as the catalog
connector and the mode, and nothing of the org they left: no installation id,
no account id. Importing it into a pod of a second organization maps each to
that org's installation, offers the importer's own accounts for the shared
one, and declares both before the agent's grant asks for the slug.

The worker, the database, the export and import jobs and the connectors module
are real; the catalog entry and the installations are rows, as the catalog
import and the Connectors page would have left them.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi import status

from lemma_pod_bundle import extract_bundle

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    start_and_plan_import,
    wait_import,
)
from app.modules.test_support.e2e.waiters import wait_for_status

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


async def _catalog_connector(db_session) -> str:
    connector_id = f"crm-{uuid4().hex[:8]}"
    db_session.add(
        Connector(
            id=connector_id,
            title="CRM",
            description="bundle connectors e2e",
            kinds=[{"kind": "http", "auth_scheme": "API_KEY"}],
            is_active=True,
        )
    )
    await db_session.commit()
    return connector_id


async def _install(db_session, org_id: str, connector_id: str) -> str:
    install = AuthConfig(
        organization_id=UUID(org_id),
        connector_id=connector_id,
        kind="http",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
        name=connector_id,
        is_default=True,
    )
    db_session.add(install)
    await db_session.commit()
    return str(install.id)


async def _connect(
    db_session, *, user_id: str, org_id: str, install_id: str, connector_id: str
) -> str:
    created = await AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    ).create(
        AccountEntity(
            user_id=UUID(user_id),
            organization_id=UUID(org_id),
            auth_config_id=UUID(install_id),
            connector_id=connector_id,
            is_default=True,
            credentials={"api_key": f"key-{uuid4().hex[:6]}"},
        )
    )
    await db_session.commit()
    return str(created.id)


async def _exported_pod(scenario, db_session, connector_id: str) -> tuple[bytes, dict]:
    """Org A's pod, declaring both connectors and granting one; exported."""
    client = scenario.owner_client
    await scenario.create_org_with_pod(name_prefix="Exporter")
    pod_id = scenario.pod_id
    install_id = await _install(db_session, scenario.org_id, connector_id)
    account_id = await _connect(
        db_session,
        user_id=scenario.owner_user["id"],
        org_id=scenario.org_id,
        install_id=install_id,
        connector_id=connector_id,
    )
    shared = await client.post(
        f"/pods/{pod_id}/connector-accounts",
        json={"account_id": account_id, "visibility": "POD"},
    )
    assert shared.status_code == status.HTTP_201_CREATED, shared.text
    for body in (
        {
            "slug": "crm",
            "mode": "PER_MEMBER",
            "required": True,
            "purpose": "Your deals.",
        },
        {
            "slug": "crm-shared",
            "mode": "SHARED",
            "shared_pod_account_id": shared.json()["id"],
        },
    ):
        declared = await client.post(
            f"/pods/{pod_id}/connectors", json={"auth_config_id": install_id, **body}
        )
        assert declared.status_code == status.HTTP_201_CREATED, declared.text
    await client.post(
        f"/pods/{pod_id}/agents",
        json={"name": "deal_desk", "instruction": "Look deals up."},
    )
    await scenario.replace_agent_resource_grants(
        agent_name="deal_desk",
        grants=[
            {
                "resource_type": "connector",
                "resource_name": "crm-shared",
                "permission_ids": ["connector.use"],
            }
        ],
    )

    started = await client.post(
        f"/pods/{pod_id}/bundle/exports", json={"include": ["connectors", "agents"]}
    )
    assert started.status_code == status.HTTP_202_ACCEPTED, started.text
    export_id = started.json()["export_id"]

    async def probe() -> dict:
        got = await client.get(f"/pods/{pod_id}/bundle/exports/{export_id}")
        assert got.status_code == status.HTTP_200_OK, got.text
        return got.json()

    ready = await wait_for_status(
        label=f"export {export_id}",
        probe=probe,
        expected={"READY"},
        timeout_seconds=120,
        interval_seconds=0.15,
    )
    parts = urlsplit(ready["download_url"])
    download = await client.get(f"{parts.path}?{parts.query}")
    assert download.status_code == status.HTTP_200_OK, download.text
    return download.content, {"install_id": install_id, "account_id": account_id}


async def _importing_pod(scenario, db_session, connector_id: str, *, installed: bool):
    """A pod in a second organization, with or without the CRM installed, and
    the importer's own account on it when it is."""
    await scenario.create_org_with_pod(name_prefix="Importer")
    if not installed:
        return None, None
    install_id = await _install(db_session, scenario.org_id, connector_id)
    account_id = await _connect(
        db_session,
        user_id=scenario.owner_user["id"],
        org_id=scenario.org_id,
        install_id=install_id,
        connector_id=connector_id,
    )
    return install_id, account_id


async def _pod_connectors(client, pod_id: str) -> dict[str, dict]:
    got = await client.get(f"/pods/{pod_id}/connectors")
    assert got.status_code == status.HTTP_200_OK, got.text
    return {item["slug"]: item for item in got.json()["items"]}


async def test_a_pods_connectors_round_trip_into_another_organization(
    scenario, db_session, worker, tmp_path
):
    connector_id = await _catalog_connector(db_session)
    zip_bytes, source = await _exported_pod(scenario, db_session, connector_id)

    # The bundle names the catalog connector and the mode -- nothing of org A.
    root = extract_bundle(zip_bytes, tmp_path / "bundle")
    assert json.loads((root / "connectors" / "crm" / "crm.json").read_text()) == {
        "connector": connector_id,
        "mode": "PER_MEMBER",
        "required": True,
        "purpose": "Your deals.",
    }
    assert json.loads(
        (root / "connectors" / "crm-shared" / "crm-shared.json").read_text()
    ) == {"connector": connector_id, "mode": "SHARED", "required": False}
    agent = json.loads((root / "agents" / "deal_desk" / "deal_desk.json").read_text())
    assert {
        "resource_type": "connector",
        "resource_name": "crm-shared",
        "permission_ids": ["connector.use"],
    } in agent["permissions"]["grants"]
    for text in (p.read_text() for p in root.rglob("*.json")):
        assert source["install_id"] not in text
        assert source["account_id"] not in text

    install_b, account_b = await _importing_pod(
        scenario, db_session, connector_id, installed=True
    )
    client, pod_b = scenario.owner_client, scenario.pod_id
    import_id = await start_and_plan_import(client, pod_b, zip_bytes)
    plan = (await client.get(f"/pods/{pod_b}/bundle/imports/{import_id}")).json()[
        "plan"
    ]
    by_slug = {c["slug"]: c for c in plan["connectors"]}
    assert by_slug["crm"]["status"] == "READY"
    assert by_slug["crm"]["auth_config_id"] == install_b
    shared = by_slug["crm-shared"]
    assert shared["status"] == "NEEDS_ACCOUNT"
    assert [o["account_id"] for o in shared["account_options"]] == [account_b]
    assert [s["kind"] for s in plan["steps"]][:2] == ["CONNECTOR", "CONNECTOR"]

    applied = await client.post(
        f"/pods/{pod_b}/bundle/imports/{import_id}/apply",
        json={"connectors": {"crm-shared": {"share_account_id": account_b}}},
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await wait_import(client, pod_b, import_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final

    declared = await _pod_connectors(client, pod_b)
    assert declared["crm"]["mode"] == "PER_MEMBER"
    assert declared["crm"]["required"] is True
    assert declared["crm"]["auth_config_id"] == install_b
    assert declared["crm-shared"]["status"] == "READY"
    accounts = (await client.get(f"/pods/{pod_b}/connector-accounts")).json()["items"]
    [shared_account] = [a for a in accounts if a["used_by"] == ["crm-shared"]]
    assert shared_account["account_id"] == account_b
    assert shared_account["source"] == "IMPORT"
    # The agent's grant named the slug, and found the connector declared.
    grants = (await client.get(f"/pods/{pod_b}/agents/deal_desk/permissions")).json()
    assert ("connector", "crm-shared") in {
        (g["resource_type"], g["resource_name"]) for g in grants["grants"]
    }


async def test_a_shared_connector_imported_with_no_account_waits_for_one(
    scenario, db_session, worker
):
    connector_id = await _catalog_connector(db_session)
    zip_bytes, _ = await _exported_pod(scenario, db_session, connector_id)
    await _importing_pod(scenario, db_session, connector_id, installed=True)
    client, pod_b = scenario.owner_client, scenario.pod_id

    import_id = await start_and_plan_import(client, pod_b, zip_bytes)
    applied = await client.post(
        f"/pods/{pod_b}/bundle/imports/{import_id}/apply", json={}
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await wait_import(client, pod_b, import_id, until={"COMPLETED", "FAILED"})

    # A warning, not a failure: the pod imports, the connector waits.
    assert final["status"] == "COMPLETED", final
    assert any(
        "crm-shared" in w and "needing an account" in w for w in final["warnings"]
    )
    declared = await _pod_connectors(client, pod_b)
    assert declared["crm-shared"]["status"] == "NEEDS_ACCOUNT"
    assert declared["crm-shared"]["shared_pod_account_id"] is None
    # Nothing of the importer's was shared on their behalf.
    accounts = (await client.get(f"/pods/{pod_b}/connector-accounts")).json()["items"]
    assert accounts == []


async def test_a_connector_with_no_installation_blocks_the_import(
    scenario, db_session, worker
):
    connector_id = await _catalog_connector(db_session)
    zip_bytes, _ = await _exported_pod(scenario, db_session, connector_id)
    await _importing_pod(scenario, db_session, connector_id, installed=False)
    client, pod_b = scenario.owner_client, scenario.pod_id

    import_id = await start_and_plan_import(client, pod_b, zip_bytes)
    plan = (await client.get(f"/pods/{pod_b}/bundle/imports/{import_id}")).json()[
        "plan"
    ]
    by_slug = {c["slug"]: c for c in plan["connectors"]}
    assert {c["status"] for c in by_slug.values()} == {"NEEDS_INSTALLATION"}
    assert "installation" in by_slug["crm"]["message"]

    refused = await client.post(
        f"/pods/{pod_b}/bundle/imports/{import_id}/apply", json={}
    )
    assert refused.status_code == 422, refused.text
    body = refused.json()
    assert body["code"] == "POD_BUNDLE_CONFIRMATION_REQUIRED"
    assert {b["slug"] for b in body["details"]["blocking"]} == {"crm", "crm-shared"}
    # Refused before anything was written.
    assert await _pod_connectors(client, pod_b) == {}
    agents = (await client.get(f"/pods/{pod_b}/agents")).json()
    assert "deal_desk" not in json.dumps(agents)

    # The importer administers this org and the connector needs no config, so
    # choosing to install Lemma's app for it unblocks the import.
    assert by_slug["crm"]["can_create_installation"] is True
    applied = await client.post(
        f"/pods/{pod_b}/bundle/imports/{import_id}/apply",
        json={
            "connectors": {
                "crm": {"create_installation": True},
                "crm-shared": {"create_installation": True},
            }
        },
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await wait_import(client, pod_b, import_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final
    declared = await _pod_connectors(client, pod_b)
    assert declared["crm"]["auth_config_id"] == declared["crm-shared"]["auth_config_id"]
