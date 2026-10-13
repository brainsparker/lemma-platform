"""A burst of function runs against one pod: queueing, deadlines and blast radius.

Each test drives the real Postgres or Redis the behaviour depends on; the
sandbox side is replaced at its HTTP and provider seams only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid7

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_runtime_quarantine import (
    ORPHANED_RUN_ERROR,
    FunctionRuntimeQuarantine,
)
from app.modules.function.application.function_runtime_route_resolver import (
    FunctionRuntimeRouteResolver,
)
from app.modules.function.config import function_settings
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.events.handlers import _fail_expired_function_runs
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)
from app.modules.function.infrastructure.models import FunctionModel, FunctionRunModel
from app.modules.function.infrastructure.pod_run_gate import PodRunGate
from app.modules.function.infrastructure.repositories import FunctionRunRepository
from app.modules.function.tests.fakes import InMemoryPodRunSlots


pytestmark = pytest.mark.e2e
_REVISION_HASH = f"sha256:{'3' * 64}"


async def _seed_function(session, *, pod_id: UUID, user_id: UUID, job: bool) -> UUID:
    function_id = uuid7()
    session.add(
        FunctionModel(
            id=function_id,
            pod_id=pod_id,
            user_id=user_id,
            name=f"fanout-{function_id.hex}",
            input_schema={},
            output_schema={},
            type=FunctionType.JOB if job else FunctionType.API,
            status=FunctionStatus.READY,
            visibility="POD",
            revision_hash=_REVISION_HASH,
        )
    )
    await session.flush()
    return function_id


async def _seed_run(
    session,
    *,
    function_id: UUID,
    user_id: UUID,
    deadline_at: datetime,
    job: bool,
    status: FunctionRunStatus = FunctionRunStatus.PENDING,
    started_at: datetime | None = None,
) -> UUID:
    run_id = uuid7()
    session.add(
        FunctionRunModel(
            id=run_id,
            function_id=function_id,
            revision_hash=_REVISION_HASH,
            user_id=user_id,
            input_data={"value": 1},
            status=status,
            job_id=f"function-run:{run_id}" if job else None,
            deadline_at=deadline_at,
            started_at=started_at,
        )
    )
    await session.flush()
    return run_id


async def _status_of(db_manager, run_ids: list[UUID]) -> dict[UUID, FunctionRunModel]:
    async with db_manager.session_factory() as session:
        rows = (
            await session.scalars(
                select(FunctionRunModel).where(FunctionRunModel.id.in_(run_ids))
            )
        ).all()
        return {row.id: row for row in rows}


async def test_execution_deadline_starts_when_the_run_starts_not_when_it_was_queued(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    """A run that waited in a burst keeps its whole execution budget."""
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    created = datetime.now(timezone.utc)
    async with db_manager.session_factory() as session:
        function_id = await _seed_function(
            session, pod_id=pod_id, user_id=user_id, job=False
        )
        run_id = await _seed_run(
            session,
            function_id=function_id,
            user_id=user_id,
            deadline_at=created + timedelta(minutes=30),
            job=True,
        )
        await session.commit()

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        dispatch = await FunctionExecutionRepository(uow).resolve_dispatch(
            run_id, mode=FunctionDispatchMode.ASYNCHRONOUS
        )
    assert isinstance(dispatch, FunctionExecutionDispatch)
    budget = function_settings.function_api_deadline_seconds
    # Started well after an API function's whole budget had passed in the queue.
    started = created + timedelta(seconds=budget + 300)
    async with factory() as uow:
        context = await FunctionExecutionRepository(uow).start_execution(
            dispatch, now=started
        )

    assert context is not None
    assert context.deadline_at == started + timedelta(seconds=budget)
    # Still inside its budget from the start, so the sweep leaves it alone.
    async with factory() as uow:
        await FunctionRunRepository(uow).fail_expired(
            now=started + timedelta(seconds=budget - 1),
            job_callback_grace_seconds=0,
        )
    row = (await _status_of(db_manager, [run_id]))[run_id]
    assert row.status == FunctionRunStatus.RUNNING
    assert row.deadline_at == started + timedelta(seconds=budget)


class _Provider:
    """The sandbox provider seam: records destroys, owns no sandboxes."""

    destroyed: list[UUID]

    def __init__(self) -> None:
        self.destroyed = []

    async def destroy_sandbox(self, workload_kind, logical_id, **_kwargs) -> None:
        del workload_kind
        self.destroyed.append(logical_id)

    async def close(self) -> None:
        return None


def _runtime_answering_probe(status_code: int) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/healthz")
        return httpx.Response(status_code, json={"ready": status_code == 200})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _endpoint() -> FunctionRuntimeEndpoint:
    return FunctionRuntimeEndpoint(
        url="https://sandbox.example.test/runtime/",
        request_headers=(),
        allocation_id=uuid4(),
        allocation_epoch=1,
        profile_digest=f"sha256:{'2' * 64}",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


async def _seed_in_flight(db_manager, *, pod_id: UUID, user_id: UUID):
    now = datetime.now(timezone.utc)
    async with db_manager.session_factory() as session:
        job_function = await _seed_function(
            session, pod_id=pod_id, user_id=user_id, job=True
        )
        api_function = await _seed_function(
            session, pod_id=pod_id, user_id=user_id, job=False
        )
        started = now - timedelta(seconds=5)
        deadline = now + timedelta(minutes=10)
        running = FunctionRunStatus.RUNNING
        jobs = [
            await _seed_run(
                session,
                function_id=job_function,
                job=True,
                user_id=user_id,
                status=running,
                started_at=started,
                deadline_at=deadline,
            )
            for _ in range(3)
        ]
        synchronous = await _seed_run(
            session,
            function_id=api_function,
            job=False,
            user_id=user_id,
            status=running,
            started_at=started,
            deadline_at=deadline,
        )
        await session.commit()
    return jobs, synchronous


async def test_a_5xx_on_a_live_runtime_leaves_the_sandbox_and_its_runs_alone(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    jobs, _ = await _seed_in_flight(
        db_manager, pod_id=pod_id, user_id=UUID(fixed_test_user["id"])
    )
    provider = _Provider()
    quarantine = FunctionRuntimeQuarantine(
        routes=FunctionRuntimeRouteResolver(
            sandbox_client_factory=lambda: provider,
            endpoint_cache=FunctionRuntimeEndpointCache(),
        ),
        uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory),
        runtime_http_client_factory=lambda: probe,
        pod_runs=InMemoryPodRunSlots(),
    )

    async with _runtime_answering_probe(200) as probe:
        quarantined = await quarantine.after_failure(pod_id, _endpoint())

    assert quarantined is False
    assert provider.destroyed == []
    rows = await _status_of(db_manager, jobs)
    assert {row.status for row in rows.values()} == {FunctionRunStatus.RUNNING}


async def test_a_dead_runtime_is_replaced_and_its_job_runs_fail_at_once(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    """Nothing will ever report for those runs; they fail now, not at the
    deadline sweep, and give their pod slots back."""
    pod_id = UUID(test_pod["id"])
    jobs, synchronous = await _seed_in_flight(
        db_manager, pod_id=pod_id, user_id=UUID(fixed_test_user["id"])
    )
    provider = _Provider()
    slots = InMemoryPodRunSlots()
    quarantine = FunctionRuntimeQuarantine(
        routes=FunctionRuntimeRouteResolver(
            sandbox_client_factory=lambda: provider,
            endpoint_cache=FunctionRuntimeEndpointCache(),
        ),
        uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory),
        runtime_http_client_factory=lambda: probe,
        pod_runs=slots,
    )

    async with _runtime_answering_probe(502) as probe:
        quarantined = await quarantine.after_failure(pod_id, _endpoint())

    assert quarantined is True
    assert provider.destroyed == [pod_id]
    rows = await _status_of(db_manager, [*jobs, synchronous])
    for run_id in jobs:
        assert rows[run_id].status == FunctionRunStatus.FAILED
        assert rows[run_id].error == ORPHANED_RUN_ERROR
    # A synchronous run holds its own connection and fails through it.
    assert rows[synchronous].status == FunctionRunStatus.RUNNING
    assert set(jobs) <= set(slots.released)


async def test_pod_gate_queues_the_run_past_the_limit_until_a_slot_frees(
    test_redis_url,
) -> None:
    redis = Redis.from_url(test_redis_url, decode_responses=True)
    gate = PodRunGate(limit=2, redis_factory=lambda: redis)
    pod_id = uuid4()
    first, second, third = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    hold = now + timedelta(minutes=5)
    try:
        assert await gate.acquire(pod_id, first, hold_until=hold, wait_until=now)
        assert await gate.acquire(pod_id, second, hold_until=hold, wait_until=now)
        # A redelivered dispatch of a run already holding a slot does not
        # take a second one.
        assert await gate.acquire(pod_id, second, hold_until=hold, wait_until=now)
        # Full: one that will not wait is turned away rather than let past.
        assert not await gate.acquire(pod_id, third, hold_until=hold, wait_until=now)

        waiting = asyncio.create_task(
            gate.acquire(
                pod_id,
                third,
                hold_until=hold,
                wait_until=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
        )
        await gate.release(pod_id, first)
        assert await asyncio.wait_for(waiting, timeout=10) is True
        assert set(await redis.zrange(f"function:pod-runs:{pod_id}", 0, -1)) == {
            str(second),
            str(third),
        }
    finally:
        await redis.delete(f"function:pod-runs:{pod_id}")
        await redis.aclose()


async def test_pod_gate_slot_of_a_dispatcher_that_died_lapses_on_its_own(
    test_redis_url,
) -> None:
    redis = Redis.from_url(test_redis_url, decode_responses=True)
    gate = PodRunGate(limit=1, redis_factory=lambda: redis)
    pod_id = uuid4()
    now = datetime.now(timezone.utc)
    try:
        assert await gate.acquire(
            pod_id, uuid4(), hold_until=now - timedelta(seconds=1), wait_until=now
        )
        # Never released, but past its hold: the next run gets the slot.
        assert await gate.acquire(
            pod_id, uuid4(), hold_until=now + timedelta(minutes=1), wait_until=now
        )
    finally:
        await redis.delete(f"function:pod-runs:{pod_id}")
        await redis.aclose()


async def test_sweeps_page_past_the_first_page(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    """Both halves of the reconcile cron reach runs beyond one page."""
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    now = datetime.now(timezone.utc)
    async with db_manager.session_factory() as session:
        function_id = await _seed_function(
            session, pod_id=pod_id, user_id=user_id, job=True
        )
        unqueued = [
            await _seed_run(
                session,
                function_id=function_id,
                user_id=user_id,
                deadline_at=now + timedelta(minutes=30),
                job=True,
            )
            for _ in range(5)
        ]
        expired = [
            await _seed_run(
                session,
                function_id=function_id,
                user_id=user_id,
                deadline_at=now - timedelta(seconds=1),
                job=True,
            )
            for _ in range(5)
        ]
        await session.commit()
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)

    seen: list[UUID] = []
    after: UUID | None = None
    while True:
        async with factory() as uow:
            page = await FunctionRunRepository(uow).list_pending_async_runs(
                now=now + timedelta(minutes=5),
                min_age_seconds=0,
                limit=2,
                after=after,
            )
        seen.extend(page)
        if len(page) < 2:
            break
        after = page[-1]
    assert set(unqueued) <= set(seen)
    assert len(seen) == len(set(seen))

    await _fail_expired_function_runs(uow_factory=factory, now=now, limit=2)
    rows = await _status_of(db_manager, expired)
    assert {row.status for row in rows.values()} == {FunctionRunStatus.FAILED}
