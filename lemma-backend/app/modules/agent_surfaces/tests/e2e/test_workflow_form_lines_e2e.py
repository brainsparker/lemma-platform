"""A workflow's form, as a line waiting on its assignee, end to end.

Assigning a FORM puts a question in the assignee's own channel; it is in
their inbox as waiting on them. Answered on the line, the engine validates it
-- a bad answer is refused and the line stays open -- and the run moves on;
answered in the workflow's own screen, the line closes all the same.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.identity.contracts.orm import OrganizationMember
from app.modules.pod.contracts.orm import PodMember
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.workflow.events.run_work import register_run_work
from app.modules.workflow.events.step_jobs import register_step_jobs

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

module_identity = e2e_fixtures.module_identity
fixed_test_user = e2e_fixtures.module_test_user
fixed_test_org = e2e_fixtures.module_test_org


async def _run_workflow_work() -> None:
    registry = WorkRegistry()
    register_run_work(registry)
    register_step_jobs(registry)
    await run_until_idle(
        async_session_maker,
        registry=registry,
        classes=[WorkClass.EVENTS, WorkClass.AUTOMATION, WorkClass.DELIVERY],
    )


async def _form_run(
    client: AsyncClient, db_session: AsyncSession, *, pod_id: str, user_id: str
) -> str:
    member_id = (
        await db_session.execute(
            select(PodMember.id)
            .join(
                OrganizationMember,
                OrganizationMember.id == PodMember.organization_member_id,
            )
            .where(
                PodMember.pod_id == UUID(pod_id),
                OrganizationMember.user_id == UUID(user_id),
            )
        )
    ).scalar_one()
    created = await client.post(
        f"/pods/{pod_id}/workflows",
        json={
            "name": "expense-approval",
            "start": {"type": "MANUAL"},
            "mode": "GLOBAL",
        },
    )
    assert created.status_code == 201, created.text
    name = created.json()["name"]
    graph = await client.put(
        f"/pods/{pod_id}/workflows/{name}/graph",
        json={
            "start": {"type": "MANUAL"},
            "nodes": [
                {
                    "id": "approve",
                    "type": "FORM",
                    "label": "Approve the expense",
                    "config": {
                        "assignee_pod_member_id": str(member_id),
                        "input_schema": {
                            "type": "object",
                            "properties": {"approved": {"type": "boolean"}},
                            "required": ["approved"],
                        },
                    },
                },
                {"id": "end", "type": "END"},
            ],
            "edges": [{"id": "e1", "source": "approve", "target": "end"}],
        },
    )
    assert graph.status_code == 200, graph.text
    run = await client.post(f"/pods/{pod_id}/workflows/{name}/runs")
    assert run.status_code == 201, run.text
    await _run_workflow_work()
    return run.json()["id"]


async def _waiting(client: AsyncClient, pod_id: str) -> list[dict]:
    inbox = await client.get(f"/pods/{pod_id}/inbox")
    assert inbox.status_code == 200, inbox.text
    return inbox.json()["waiting"]


async def test_a_form_waits_on_its_assignee_and_is_answered_on_its_line(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    run_id = await _form_run(
        authenticated_client, db_session, pod_id=pod_id, user_id=fixed_test_user["id"]
    )
    [waiting] = await _waiting(authenticated_client, pod_id)
    asked = waiting["message"]
    assert waiting["channel"]["kind"] == "DIRECT"
    assert asked["interaction"]["kind"] == "FORM"
    question = asked["interaction"]["question"]
    assert (question["run_id"], question["node_id"]) == (run_id, "approve")
    assert question["schema"]["properties"]["approved"]
    answer = (
        f"/pods/{pod_id}/channels/{waiting['channel']['id']}"
        f"/messages/{asked['id']}/answer"
    )

    refused = await authenticated_client.post(
        answer, json={"answer": {"approved": "maybe"}}
    )
    assert refused.status_code == 422, refused.text
    assert len(await _waiting(authenticated_client, pod_id)) == 1

    answered = await authenticated_client.post(
        answer, json={"answer": {"approved": True}}
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["interaction"]["state"] == "ANSWERED"
    assert await _waiting(authenticated_client, pod_id) == []
    await _run_workflow_work()
    finished = await authenticated_client.get(f"/pods/{pod_id}/workflow-runs/{run_id}")
    assert finished.json()["status"] == "COMPLETED", finished.text


async def test_a_form_submitted_in_the_workflow_closes_its_line(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    run_id = await _form_run(
        authenticated_client, db_session, pod_id=pod_id, user_id=fixed_test_user["id"]
    )
    assert len(await _waiting(authenticated_client, pod_id)) == 1
    submitted = await authenticated_client.post(
        f"/pods/{pod_id}/workflow-runs/{run_id}/form",
        json={"node_id": "approve", "inputs": {"approved": False}},
    )
    assert submitted.status_code == 200, submitted.text
    assert await _waiting(authenticated_client, pod_id) == []
