"""An app's grants travel in its bundle, and an import cannot hand out more.

An app's page acts for whoever views it, within the app's own grants
(``APP_NOT_GRANTED`` outside them). A pod exported and imported elsewhere must
bring those grants along -- the pod grant included -- or the imported app is
refused everything it was built to do.

Setting them is conferral (PS-ACCESS-010): an import sets them on the
importer's behalf, so an importer who could not make a grant themselves is
refused in the plan, before anything is written, rather than by the apply's
last step with the pod half-changed.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import status
from lemma_pod_bundle import pack_bundle

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    BUNDLES_DIR,
    import_and_apply,
    new_pod,
    pack_fixture_bundle,
    wait_import,
)
from app.modules.test_support.e2e.waiters import wait_for_status
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = [pytest.mark.e2e, pytest.mark.worker]

POD_GRANT = {
    "resource_type": "pod",
    "resource_name": "pod",
    "permission_ids": ["datastore.table.read", "pod.read"],
}


def _by_target(grants: list[dict]) -> dict[tuple[str, str], list[str]]:
    return {
        (g["resource_type"], g["resource_name"]): sorted(g["permission_ids"])
        for g in grants
    }


async def _export_url(client, pod_id: str) -> str:
    start = await client.post(f"/pods/{pod_id}/bundle/exports", json={})
    assert start.status_code == status.HTTP_202_ACCEPTED, start.text
    export_id = start.json()["export_id"]

    async def probe() -> dict:
        return (await client.get(f"/pods/{pod_id}/bundle/exports/{export_id}")).json()

    final = await wait_for_status(
        label=f"pod {pod_id} bundle export {export_id}",
        probe=probe,
        expected={"READY", "FAILED"},
        failed=set(),
        timeout_seconds=60,
        interval_seconds=0.15,
    )
    assert final["status"] == "READY", final
    return final["download_url"]


async def test_an_apps_grants_survive_export_and_import(
    authenticated_client, test_pod, fixed_test_org, worker
):
    client = authenticated_client
    source_id = test_pod["id"]
    await import_and_apply(client, source_id, pack_fixture_bundle("with_app_static"))
    table = await client.post(
        f"/pods/{source_id}/datastore/tables",
        json={"name": "leads", "columns": [{"name": "note", "type": "TEXT"}]},
    )
    assert table.status_code == status.HTTP_201_CREATED, table.text
    granted = {
        "grants": [
            POD_GRANT,
            {
                "resource_type": "datastore_table",
                "resource_name": "leads",
                "permission_ids": ["datastore.record.read"],
            },
        ]
    }
    put = await client.put(
        f"/pods/{source_id}/apps/dashboard/permissions", json=granted
    )
    assert put.status_code == status.HTTP_200_OK, put.text

    download_url = await _export_url(client, source_id)
    target_id = await new_pod(client, fixed_test_org["id"], label="App Grants")
    res = await client.post(
        f"/pods/{target_id}/bundle/imports", json={"kind": "URL", "url": download_url}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    import_id = res.json()["import_id"]
    planned = await wait_import(
        client, target_id, import_id, until={"AWAITING_CONFIRMATION", "FAILED"}
    )
    assert planned["status"] == "AWAITING_CONFIRMATION", planned
    grant_steps = [s for s in planned["plan"]["steps"] if s["kind"] == "APP_GRANTS"]
    assert [s["name"] for s in grant_steps] == ["dashboard"]
    assert not grant_steps[0]["detail"].get("refused"), grant_steps

    apply = await client.post(
        f"/pods/{target_id}/bundle/imports/{import_id}/apply", json={}
    )
    assert apply.status_code == status.HTTP_202_ACCEPTED, apply.text
    final = await wait_import(
        client, target_id, import_id, until={"COMPLETED", "FAILED"}
    )
    assert final["status"] == "COMPLETED", final

    got = await client.get(f"/pods/{target_id}/apps/dashboard/permissions")
    assert got.status_code == status.HTTP_200_OK, got.text
    assert _by_target(got.json()["grants"]) == _by_target(granted["grants"])


def _bundle_granting(tmp_path: Path, grants: list[dict]) -> bytes:
    """The static-app fixture, its app granted ``grants``."""
    import json

    root = tmp_path / "bundle"
    shutil.copytree(BUNDLES_DIR / "with_app_static", root)
    manifest = root / "apps" / "dashboard" / "dashboard.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["public_slug"] = f"dashboard-{uuid4().hex[:8]}"
    payload["permissions"] = {"grants": grants}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return pack_bundle(root)


async def _importer_without(
    owner,
    async_client,
    *,
    org_id: str,
    pod_id: str,
    lacking: str,
    also: tuple[str, ...] = (),
) -> dict[str, str]:
    """A member who may import into the pod and edit its apps, but does not
    hold ``lacking`` there."""
    held = [
        "pod.read",
        "pod.update",
        "app.create",
        "app.read",
        "app.update",
        "datastore.table.read",
        "function.read",
        "agent.read",
        "workflow.read",
        "schedule.read",
        *also,
    ]
    assert lacking not in held
    role = await owner.post(
        f"/pods/{pod_id}/roles",
        json={"name": "bundle_importer", "permission_ids": held},
    )
    assert role.status_code == status.HTTP_201_CREATED, role.text
    user = await signup_user(async_client, "bundle-importer")
    member = await invite_org_member(owner, async_client, org_id=org_id, user=user)
    await add_pod_member(
        owner,
        pod_id=pod_id,
        organization_member_id=member["id"],
        role="BUNDLE_IMPORTER",
        roles=["BUNDLE_IMPORTER"],
    )
    return auth_headers(user)


async def test_an_importer_cannot_grant_an_app_what_they_do_not_hold(
    authenticated_client, async_client, fixed_test_org, worker, tmp_path
):
    owner = authenticated_client
    org_id = fixed_test_org["id"]
    pod_id = await new_pod(owner, org_id, label="Grant Refusal")
    headers = await _importer_without(
        owner, async_client, org_id=org_id, pod_id=pod_id, lacking="workflow.execute"
    )
    zip_bytes = _bundle_granting(
        tmp_path,
        [
            {
                "resource_type": "pod",
                "resource_name": "pod",
                "permission_ids": ["pod.read", "workflow.execute"],
            }
        ],
    )

    up = await async_client.post(
        f"/pods/{pod_id}/bundle/uploads",
        files={"data": ("bundle.zip", zip_bytes, "application/zip")},
        headers=headers,
    )
    assert up.status_code == status.HTTP_201_CREATED, up.text
    res = await async_client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "URL", "url": up.json()["url"]},
        headers=headers,
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    import_id = res.json()["import_id"]

    # Read as the owner: the poll is the same either way, and the helper's
    # client is the owner's.
    planned = await wait_import(
        owner, pod_id, import_id, until={"AWAITING_CONFIRMATION", "FAILED"}
    )
    assert planned["status"] == "AWAITING_CONFIRMATION", planned
    step = next(s for s in planned["plan"]["steps"] if s["kind"] == "APP_GRANTS")
    assert step["detail"]["refused"] == ["workflow.execute on pod 'pod'"], step
    assert "could not use" in (step["error"] or "")
    assert any("workflow.execute" in w for w in planned["plan"]["warnings"])

    apply = await async_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={}, headers=headers
    )
    assert apply.status_code == status.HTTP_403_FORBIDDEN, apply.text
    body = apply.json()
    assert body["code"] == "CONFERRAL_EXCEEDS_HOLDER", body
    assert body["details"]["refused"] == [
        "app 'dashboard': workflow.execute on pod 'pod'"
    ]

    # Refused before anything was written: the bundle's app is not there.
    missing = await owner.get(f"/pods/{pod_id}/apps/dashboard")
    assert missing.status_code == status.HTTP_404_NOT_FOUND, missing.text
    still = await owner.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
    assert still.json()["status"] == "AWAITING_CONFIRMATION"


async def test_an_importer_cannot_grant_a_bundled_agent_what_they_do_not_hold(
    authenticated_client, async_client, fixed_test_org, worker, tmp_path
):
    """The same rule for an agent's grants: they are set on the importer's
    behalf, so a grant the importer could not make is refused and never
    written -- the bundle is no way round the permissions routes."""
    import json

    owner = authenticated_client
    org_id = fixed_test_org["id"]
    pod_id = await new_pod(owner, org_id, label="Agent Grant Refusal")
    headers = await _importer_without(
        owner,
        async_client,
        org_id=org_id,
        pod_id=pod_id,
        lacking="workflow.execute",
        also=("agent.create", "agent.update"),
    )
    root = tmp_path / "bundle"
    shutil.copytree(BUNDLES_DIR / "with_app_static", root)
    shutil.rmtree(root / "apps")
    agent_dir = root / "agents" / "helper"
    agent_dir.mkdir(parents=True)
    (agent_dir / "helper.json").write_text(
        json.dumps(
            {
                "name": "helper",
                "instruction": "Help.",
                "permissions": {
                    "grants": [
                        {
                            "resource_type": "pod",
                            "resource_name": "pod",
                            "permission_ids": ["workflow.execute"],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    up = await async_client.post(
        f"/pods/{pod_id}/bundle/uploads",
        files={"data": ("bundle.zip", pack_bundle(root), "application/zip")},
        headers=headers,
    )
    assert up.status_code == status.HTTP_201_CREATED, up.text
    res = await async_client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "URL", "url": up.json()["url"]},
        headers=headers,
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    import_id = res.json()["import_id"]
    planned = await wait_import(
        owner, pod_id, import_id, until={"AWAITING_CONFIRMATION", "FAILED"}
    )
    assert planned["status"] == "AWAITING_CONFIRMATION", planned
    apply = await async_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={}, headers=headers
    )
    assert apply.status_code == status.HTTP_202_ACCEPTED, apply.text
    ended = await wait_import(
        owner,
        pod_id,
        import_id,
        until={"COMPLETED", "FAILED", "PARTIALLY_COMPLETED", "COMPLETED_WITH_ERRORS"},
    )
    grants_step = next(s for s in ended["plan"]["steps"] if s["kind"] == "AGENT_GRANTS")
    assert grants_step["status"] != "DONE", ended
    assert "could not use" in (grants_step["error"] or ""), grants_step

    # Nothing of it was written: the agent holds no grant on the pod.
    held = await owner.get(f"/pods/{pod_id}/agents/helper/permissions")
    assert held.status_code == status.HTTP_200_OK, held.text
    assert not [g for g in held.json()["grants"] if g["resource_type"] == "pod"], (
        held.json()
    )
