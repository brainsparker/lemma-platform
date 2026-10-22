"""Use-case e2e: bundled connector-bound resources name their pod connector.

A surface or schedule carries the pod connector it runs through by slug, which
is portable as it stands -- the importing pod's connector decides the account
-- so a plan asks for no account at all. A bundle that still asks for one
predates that and is refused at planning, naming the variable.
"""

from __future__ import annotations

import pytest
from fastapi import status

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    pack_fixture_bundle,
    start_and_plan_import,
    wait_import,
)

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


async def test_a_bundled_surface_names_its_pod_connector_and_asks_for_no_account(
    authenticated_client, test_pod, worker
):
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("with_connectors")
    import_id = await start_and_plan_import(authenticated_client, pod_id, zip_bytes)

    got = await authenticated_client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
    assert got.status_code == status.HTTP_200_OK, got.text
    plan = got.json()["plan"]
    assert plan["variables"] == [], plan["variables"]
    assert any(s["kind"] == "SURFACE" for s in plan["steps"]), plan["steps"]


async def test_a_bundled_schedule_names_its_pod_connector_and_asks_for_no_account(
    authenticated_client, test_pod, worker
):
    """A schedule carries its pod connector by slug, which is portable as it
    stands: the plan asks for no account at all. Applying it into a pod that
    declares no such connector fails, naming the connector -- never a schedule
    created listening through nothing."""
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("with_connector_schedule")
    import_id = await start_and_plan_import(authenticated_client, pod_id, zip_bytes)

    got = await authenticated_client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
    assert got.status_code == status.HTTP_200_OK, got.text
    plan = got.json()["plan"]
    assert not [v for v in plan["variables"] if v["kind"] == "account"], plan
    assert any(s["kind"] == "SCHEDULE" for s in plan["steps"]), plan["steps"]

    apply = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={"variables": {}}
    )
    assert apply.status_code == status.HTTP_202_ACCEPTED, apply.text
    body = await wait_import(
        authenticated_client, pod_id, import_id, until={"FAILED", "SUCCEEDED"}
    )
    assert body["status"] == "FAILED", body
    assert "jira" in (body.get("error") or ""), body


async def test_a_bundle_asking_for_an_account_is_refused_at_planning(
    authenticated_client, test_pod, worker
):
    """Its resources would be created wired to an account id nothing reads any
    more, so it fails planning -- before anything is written -- and says to
    re-export."""
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("asks_for_an_account")

    up = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/uploads",
        files={"data": ("bundle.zip", zip_bytes, "application/zip")},
    )
    assert up.status_code == status.HTTP_201_CREATED, up.text
    res = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports", json={"kind": "URL", "url": up.json()["url"]}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    import_id = res.json()["import_id"]

    body = await wait_import(authenticated_client, pod_id, import_id, until={"FAILED"})
    assert body["status"] == "FAILED"
    assert "on_ticket_account" in (body.get("error") or "")
    assert "pod connector" in (body.get("error") or "")
