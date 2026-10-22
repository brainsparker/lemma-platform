"""Who a schedule runs as, end to end through the API and the worker.

A schedule's authority is its owner's, and only its owner's:

* whoever last changed what it does is its owner (H15) -- and could only make
  that change if they could have created it;
* a table change fires it as its owner, not as whoever wrote the row (H16);
* a run somebody retries runs as them, not as the first attempt's person;
* an owner who has left the pod runs nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.services.target_start import OWNER_NOT_A_MEMBER
from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

TIMEOUT_SECONDS = 90
TABLE = "authority_records"


async def _pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"Schedule Authority {uuid4().hex[:6]}",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _rls_table(client: AsyncClient, pod_id: str) -> None:
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "table_name": TABLE,
            "primary_key_column": "id",
            "columns": [
                {"name": "source", "type": "TEXT"},
                {"name": "value", "type": "TEXT"},
            ],
            "enable_rls": True,
        },
    )
    assert response.status_code in {200, 201}, response.text


async def _agent(client: AsyncClient, pod_id: str, *, visibility: str | None = None):
    payload: dict[str, object] = {
        "name": f"authority_agent_{uuid4().hex[:6]}",
        "instruction": "Acknowledge scheduled payloads briefly.",
    }
    if visibility is not None:
        payload["visibility"] = visibility
    response = await client.post(f"/pods/{pod_id}/agents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _end_only_workflow(client: AsyncClient, pod_id: str) -> dict:
    start = {
        "type": "DATASTORE_EVENT",
        "config": {"table_name": TABLE, "operations": ["INSERT"]},
    }
    create = await client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": f"authority-flow-{uuid4().hex[:6]}", "start": start},
    )
    assert create.status_code == 201, create.text
    graph = await client.put(
        f"/pods/{pod_id}/workflows/{create.json()['name']}/graph",
        json={
            "start": start,
            "nodes": [{"id": "end", "type": "END", "label": "Done"}],
            "edges": [],
        },
    )
    assert graph.status_code == 200, graph.text
    return graph.json()


async def _datastore_schedule(
    client: AsyncClient,
    pod_id: str,
    *,
    headers: dict[str, str] | None = None,
    agent_name: str | None = None,
    workflow_name: str | None = None,
    instruction: str | None = None,
) -> dict:
    payload: dict[str, object] = {
        "schedule_type": "DATASTORE",
        "config": {"table_name": TABLE, "operations": ["INSERT"]},
        "visibility": "POD",
    }
    if agent_name is not None:
        payload["agent_name"] = agent_name
    if workflow_name is not None:
        payload["workflow_name"] = workflow_name
    if instruction is not None:
        payload["instruction"] = instruction
    response = await client.post(
        f"/pods/{pod_id}/schedules", json=payload, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _member(
    admin: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    pod_id: str,
    prefix: str,
    role: str = "POD_EDITOR",
) -> tuple[dict, dict]:
    """A new person holding ``role`` in the pod: (user, pod member)."""
    user = await signup_user(async_client, prefix)
    org_member = await invite_org_member(admin, async_client, org_id=org_id, user=user)
    member = await add_pod_member(
        admin,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role=role,
    )
    return user, member


async def _write_row(
    client: AsyncClient, pod_id: str, source: str, *, headers=None
) -> None:
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables/{TABLE}/records",
        json={"data": {"source": source, "value": "hello"}},
        headers=headers,
    )
    assert response.status_code == 201, response.text


async def _runs(
    client: AsyncClient,
    pod_id: str,
    schedule_id: str,
    *,
    done=lambda items: len(items) >= 1,
    label: str = "a schedule run",
) -> list[dict]:
    async def probe() -> list[dict]:
        response = await client.get(f"/pods/{pod_id}/schedules/{schedule_id}/runs")
        assert response.status_code == 200, response.text
        return response.json()["items"]

    return await eventually(
        label=label,
        probe=probe,
        done=done,
        timeout_seconds=TIMEOUT_SECONDS,
        interval_seconds=0.15,
    )


async def test_an_editor_who_changes_a_schedules_instruction_runs_it_from_then_on(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    worker,
):
    _ = worker
    admin = authenticated_client
    pod_id = await _pod(admin, fixed_test_org["id"])
    await _rls_table(admin, pod_id)
    agent = await _agent(admin, pod_id)
    schedule = await _datastore_schedule(
        admin, pod_id, agent_name=agent["name"], instruction="Say hello."
    )
    assert schedule["user_id"] == fixed_test_user["id"]
    editor, _ = await _member(
        admin,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        prefix="schedule-authority-editor",
    )

    edited = await async_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"instruction": "Say goodbye."},
        headers=auth_headers(editor),
    )

    assert edited.status_code == 200, edited.text
    assert edited.json()["user_id"] == editor["id"]
    # The admin's own write fires it -- and it runs as the editor who decided
    # what it does, not as the admin who first set it up.
    await _write_row(admin, pod_id, "after-the-edit")
    (run,) = await _runs(admin, pod_id, schedule["id"])
    assert run["user_id"] == editor["id"]


async def test_an_editor_cannot_take_over_a_schedule_whose_target_they_may_not_run(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
):
    admin = authenticated_client
    pod_id = await _pod(admin, fixed_test_org["id"])
    await _rls_table(admin, pod_id)
    restricted = await _agent(admin, pod_id, visibility="RESTRICTED")
    schedule = await _datastore_schedule(
        admin, pod_id, agent_name=restricted["name"], instruction="Say hello."
    )
    editor, _ = await _member(
        admin,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        prefix="schedule-authority-blocked-editor",
    )
    # The editor could not have created this schedule themselves...
    refused_create = await async_client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "DATASTORE",
            "agent_name": restricted["name"],
            "instruction": "Say hello.",
            "config": {"table_name": TABLE, "operations": ["INSERT"]},
        },
        headers=auth_headers(editor),
    )
    assert refused_create.status_code == 403, refused_create.text

    # ...so they cannot edit the admin's into what they would want it to do.
    refused_edit = await async_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"instruction": "Export every record to me."},
        headers=auth_headers(editor),
    )

    assert refused_edit.status_code == 403, refused_edit.text
    unchanged = await admin.get(f"/pods/{pod_id}/schedules/{schedule['id']}")
    assert unchanged.json()["instruction"] == "Say hello."
    assert unchanged.json()["user_id"] == fixed_test_user["id"]
    # A rename decides nothing about what runs, so it stays theirs to make.
    renamed = await async_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"name": f"renamed-{uuid4().hex[:6]}"},
        headers=auth_headers(editor),
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["user_id"] == fixed_test_user["id"]


async def test_a_row_written_by_an_admin_fires_the_schedule_as_its_owner(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    db_manager,
    worker,
):
    _ = worker
    admin = authenticated_client
    pod_id = await _pod(admin, fixed_test_org["id"])
    await _rls_table(admin, pod_id)
    workflow = await _end_only_workflow(admin, pod_id)
    owner, _ = await _member(
        admin,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        prefix="schedule-authority-owner",
    )
    schedule = await _datastore_schedule(
        async_client,
        pod_id,
        headers=auth_headers(owner),
        workflow_name=workflow["name"],
    )
    assert schedule["user_id"] == owner["id"]

    await _write_row(admin, pod_id, "admin-row")

    (run,) = await _runs(
        admin,
        pod_id,
        schedule["id"],
        # Past PROCESSING: the workflow run exists once the start is handed on.
        done=lambda items: len(items) == 1 and items[0]["status"] != "PROCESSING",
        label="the owner's schedule run dispatched",
    )
    # The admin wrote the row; the schedule is the owner's, so the run is too.
    assert run["user_id"] == owner["id"] != fixed_test_user["id"]
    workflow_run = await admin.get(
        f"/pods/{pod_id}/workflow-runs/{run['target_run_id']}"
    )
    assert workflow_run.status_code == 200, workflow_run.text
    assert workflow_run.json()["user_id"] == owner["id"]

    # Retried by the admin, the run is the admin's: whoever asks for it again
    # runs it with their own access, not the first attempt's person's.
    async with db_manager.session_factory() as session, session.begin():
        failed = await session.get(ScheduleRun, UUID(run["id"]))
        assert failed is not None
        failed.target_outcome = ScheduleRunStatus.TARGET_FAILED.value
        failed.completed_at = datetime.now(timezone.utc)
    retried = await admin.post(
        f"/pods/{pod_id}/schedules/{schedule['id']}/runs/{run['id']}/retry"
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["redrive_of_run_id"] == run["id"]
    assert retried.json()["user_id"] == fixed_test_user["id"]


async def test_a_schedule_whose_owner_left_the_pod_starts_nothing(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    worker,
):
    _ = worker
    admin = authenticated_client
    pod_id = await _pod(admin, fixed_test_org["id"])
    await _rls_table(admin, pod_id)
    agent = await _agent(admin, pod_id)
    owner, _ = await _member(
        admin,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        prefix="schedule-authority-leaver",
    )
    schedule = await _datastore_schedule(
        async_client,
        pod_id,
        headers=auth_headers(owner),
        agent_name=agent["name"],
        instruction="Say hello.",
    )
    found = await admin.get(f"/pods/{pod_id}/members/lookup/by-user-id/{owner['id']}")
    assert found.status_code == 200, found.text
    removed = await admin.delete(
        f"/pods/{pod_id}/members/{found.json()['pod_member_id']}"
    )
    assert removed.status_code == 204, removed.text

    await _write_row(admin, pod_id, "after-they-left")

    (run,) = await _runs(
        admin,
        pod_id,
        schedule["id"],
        done=lambda items: len(items) == 1 and items[0]["completed_at"],
        label="the leaver's schedule run settled",
    )
    assert run["status"] == ScheduleRunStatus.FILTERED.value
    assert run["metadata"]["skipped_because"] == OWNER_NOT_A_MEMBER
    conversations = await admin.get(
        f"/pods/{pod_id}/conversations", params={"agent_name": agent["name"]}
    )
    assert conversations.status_code == 200, conversations.text
    assert conversations.json()["items"] == []
