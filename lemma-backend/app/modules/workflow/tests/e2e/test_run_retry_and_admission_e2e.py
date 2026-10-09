"""Retrying a failed run where it failed, queueing past a run limit, and a
reconciliation sweep that reaches every stale wait.

Each drives the real engine against Postgres. Agent and function completions
are driven through the workflow's own event handlers, as the other workflow
e2e tests do, so no model or worker is needed; only creating a function needs
the sandbox, for schema extraction.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid7

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.function.domain.entities import (
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.infrastructure.models import FunctionModel, FunctionRunModel
from app.modules.workflow.api.dependencies import build_workflow_engine
from app.modules.workflow.infrastructure.models import (
    WorkflowRunModel,
    WorkflowRunWaitModel,
)
from app.modules.workflow.services.run_resume_service import (
    RECONCILE_BATCH,
    RunResumeService,
)
from app.modules.workflow.tests.e2e.test_flow_execution_e2e import (
    _complete_agent_conversation,
    _create_agent,
    _create_echo_function,
    _create_pod,
    _create_run,
    _create_workflow,
    _drive_function_completed,
    _drive_function_failed,
    _fail_agent_conversation,
    _get_run,
    _submit_form,
)

pytestmark = [pytest.mark.e2e]


async def _retry(client: AsyncClient, pod_id: str, run_id: str, expected: int) -> dict:
    response = await client.post(f"/pods/{pod_id}/workflow-runs/{run_id}/retry")
    assert response.status_code == expected, response.text
    return response.json()


async def _agent_then_function_workflow(client: AsyncClient, pod_id: str) -> str:
    agent_name = await _create_agent(client, pod_id)
    function_name = await _create_echo_function(client, pod_id, function_type="JOB")
    workflow = await _create_workflow(
        client,
        pod_id,
        name=f"retry-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "draft",
                "type": "AGENT",
                "config": {"agent_name": agent_name, "input_mapping": {}},
            },
            {
                "id": "record",
                "type": "FUNCTION",
                "config": {
                    "function_name": function_name,
                    "input_mapping": {
                        "merchant": {"type": "literal", "value": "Uber"},
                        "amount": {"type": "literal", "value": 23.5},
                        "kind": {"type": "literal", "value": "expense"},
                    },
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[
            {"id": "e1", "source": "draft", "target": "record"},
            {"id": "e2", "source": "record", "target": "end"},
        ],
    )
    return workflow["name"]


@pytest.mark.asyncio
async def test_retry_resumes_at_the_failed_function_without_rerunning_the_agent(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    _ = configure_workspace_api_url
    client = authenticated_client
    pod_id = await _create_pod(client, fixed_test_org["id"], "Workflow Retry")
    workflow_name = await _agent_then_function_workflow(client, pod_id)

    run = await _create_run(client, pod_id, workflow_name)
    conversation_id = run["active_wait"]["external_ref"]
    draft = {"items": [{"merchant": "Uber"}]}
    await _complete_agent_conversation(conversation_id, draft)
    run = await _get_run(client, pod_id, run["id"])
    first_function_run = run["active_wait"]["external_ref"]
    await _drive_function_failed(first_function_run, "function runtime returned 500")

    failed = await _get_run(client, pod_id, run["id"])
    assert failed["status"] == "FAILED"
    assert failed["failed_node_id"] == "record"

    retried = await _retry(client, pod_id, run["id"], 200)

    assert retried["status"] == "RUNNING"
    assert retried["error"] is None
    assert retried["active_wait"]["wait_type"] == "FUNCTION"
    second_function_run = retried["active_wait"]["external_ref"]
    assert second_function_run != first_function_run
    # The agent's answer is reused, not asked for again.
    assert retried["execution_context"]["draft"] == draft
    assert [step["node_id"] for step in retried["step_history"]].count("draft") == 1

    await _drive_function_completed(
        second_function_run,
        {"merchant": "Uber", "amount": 23.5, "kind": "expense", "recorded": True},
    )
    completed = await _get_run(client, pod_id, run["id"])
    assert completed["status"] == "COMPLETED"
    assert completed["execution_context"]["draft"] == draft
    assert completed["execution_context"]["record"]["recorded"] is True

    # Only a failed run can be retried.
    await _retry(client, pod_id, run["id"], 409)


@pytest.mark.asyncio
async def test_a_run_that_failed_at_its_agent_is_not_retried(
    authenticated_client: AsyncClient,
    fixed_test_org,
    configure_workspace_api_url,
):
    """Retrying there would pay for the model work again; run it again instead."""
    _ = configure_workspace_api_url
    client = authenticated_client
    pod_id = await _create_pod(client, fixed_test_org["id"], "Workflow No Retry")
    workflow_name = await _agent_then_function_workflow(client, pod_id)

    run = await _create_run(client, pod_id, workflow_name)
    await _fail_agent_conversation(run["active_wait"]["external_ref"], "model down")
    failed = await _get_run(client, pod_id, run["id"])
    assert failed["failed_node_id"] == "draft"

    await _retry(client, pod_id, run["id"], 409)
    assert (await _get_run(client, pod_id, run["id"]))["status"] == "FAILED"


async def _form_workflow(
    client: AsyncClient, pod_id: str, *, limit: int | None
) -> dict:
    workflow = await _create_workflow(
        client,
        pod_id,
        name=f"limited-{uuid4().hex[:6]}",
        start={"type": "MANUAL"},
        nodes=[
            {
                "id": "approve",
                "type": "FORM",
                "config": {
                    "input_schema": {
                        "type": "object",
                        "properties": {"ok": {"type": "boolean"}},
                    }
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "e1", "source": "approve", "target": "end"}],
    )
    if limit is None:
        return workflow
    patched = await client.patch(
        f"/pods/{pod_id}/workflows/{workflow['name']}",
        json={"max_concurrent_runs": limit},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["max_concurrent_runs"] == limit
    return patched.json()


@pytest.mark.asyncio
async def test_a_run_past_the_limit_waits_its_turn_instead_of_failing(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    client = authenticated_client
    pod_id = await _create_pod(client, fixed_test_org["id"], "Workflow Limit")
    workflow_name = (await _form_workflow(client, pod_id, limit=1))["name"]

    first = await _create_run(client, pod_id, workflow_name)
    assert first["status"] == "WAITING"
    second = await _create_run(client, pod_id, workflow_name)
    assert second["status"] == "RUNNING"
    assert second["active_wait"]["wait_type"] == "QUEUED"
    assert second["step_history"] == []

    await _submit_form(
        client, pod_id, first["id"], node_id="approve", inputs={"ok": True}
    )

    admitted = await _get_run(client, pod_id, second["id"])
    assert admitted["status"] == "WAITING"
    assert admitted["active_wait"]["wait_type"] == "HUMAN"
    assert admitted["active_wait"]["node_id"] == "approve"


async def _seed_in_progress_function_waits(
    *, pod_id: UUID, flow_id: UUID, user_id: UUID, count: int, created_at: datetime
) -> None:
    """Stale waits the sweep can do nothing about: their function is running."""
    async with async_session_maker() as session:
        function_id = uuid7()
        session.add(
            FunctionModel(
                id=function_id,
                pod_id=pod_id,
                user_id=user_id,
                name=f"slow-{function_id.hex}",
                input_schema={},
                output_schema={},
                type=FunctionType.JOB,
                status=FunctionStatus.READY,
                visibility="POD",
                revision_hash=f"sha256:{'4' * 64}",
            )
        )
        function_run_id = uuid7()
        session.add(
            FunctionRunModel(
                id=function_run_id,
                function_id=function_id,
                revision_hash=f"sha256:{'4' * 64}",
                user_id=user_id,
                input_data={},
                status=FunctionRunStatus.RUNNING,
                job_id=f"function-run:{function_run_id}",
                started_at=datetime.now(timezone.utc),
                deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        for _ in range(count):
            run_id = uuid7()
            session.add(
                WorkflowRunModel(
                    id=run_id,
                    flow_id=flow_id,
                    pod_id=pod_id,
                    user_id=user_id,
                    status="RUNNING",
                    current_node_id="approve",
                    # The run columns are naive timestamps, unlike the wait's.
                    started_at=created_at.replace(tzinfo=None),
                )
            )
            session.add(
                WorkflowRunWaitModel(
                    id=uuid7(),
                    run_id=run_id,
                    flow_id=flow_id,
                    pod_id=pod_id,
                    node_id="approve",
                    wait_type="FUNCTION",
                    status="ACTIVE",
                    external_ref=str(function_run_id),
                    payload={},
                    created_at=created_at,
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_reconcile_sweep_reaches_a_stale_wait_behind_a_full_page(
    authenticated_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
):
    """More un-actionable stale waits than one page must not hide a newer one.

    The sweep used to read the oldest page and stop, so waits it could do
    nothing about -- here, ones whose function is still running -- held the
    head of the list forever. Behind them sits a queued run whose admission
    was lost: its workflow has room, and only the sweep can let it in.
    """
    client = authenticated_client
    pod_id = await _create_pod(client, fixed_test_org["id"], "Workflow Sweep")
    workflow_name = (await _form_workflow(client, pod_id, limit=1))["name"]
    # The head of the list belongs to another workflow, so it does not use up
    # the limited one's place.
    unlimited = await _form_workflow(client, pod_id, limit=None)
    first = await _create_run(client, pod_id, workflow_name)
    parked = await _create_run(client, pod_id, workflow_name)
    assert parked["active_wait"]["wait_type"] == "QUEUED"
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        # The first run ends behind the engine's back, so nothing admits the
        # parked one: the lost-admission case the sweep exists for.
        await session.execute(
            update(WorkflowRunModel)
            .where(WorkflowRunModel.id == UUID(first["id"]))
            .values(status="CANCELLED")
        )
        await session.execute(
            update(WorkflowRunWaitModel)
            .where(WorkflowRunWaitModel.run_id == UUID(first["id"]))
            .values(status="CANCELLED")
        )
        await session.execute(
            update(WorkflowRunWaitModel)
            .where(WorkflowRunWaitModel.run_id == UUID(parked["id"]))
            .values(created_at=now - timedelta(hours=1))
        )
        await session.commit()
    await _seed_in_progress_function_waits(
        pod_id=UUID(pod_id),
        flow_id=UUID(unlimited["id"]),
        user_id=UUID(fixed_test_user["id"]),
        count=RECONCILE_BATCH + 1,
        created_at=now - timedelta(hours=2),
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        await RunResumeService(build_workflow_engine(uow)).reconcile_stale_waits()

    admitted = await _get_run(client, pod_id, parked["id"])
    assert admitted["status"] == "WAITING"
    assert admitted["active_wait"]["wait_type"] == "HUMAN"
