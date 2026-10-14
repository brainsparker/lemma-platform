"""Which runs the orphan sweep is allowed to fail, decided by real SQL.

The sweep fails RUNNING and STOP_REQUESTED runs that no worker will ever
finish, and it does that from three candidate queries. The logic around them is
unit-tested against a fake repository, which hands back whatever list it is
told to -- so the one question a fake cannot answer is whether the SQL selects
the right rows. A predicate flipped from ``<`` to ``>`` fails every *young*
live run, and every test written against a stand-in still passes.

So each query is run here against Postgres, over runs seeded either side of
each threshold and in every status. The margins are a few seconds wide on
purpose: the seed and the query read the clock at different moments, and a
margin of zero would make a correct query flaky.

The three answers also have to partition the active runs: a run decided by
age alone must not also be offered to the liveness check, or it is decided
twice, and a run in neither is never decided at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import update

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    AgentRunStatus,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    running_run,
    seeded_conversation,
)
from app.modules.test_support.e2e.builders import E2EScenario

pytestmark = [pytest.mark.e2e]

#: The two ages the sweep asks about: past the first a run has had time to renew
#: its job heartbeat, past the second it is failed on age alone.
_UNRESPONSIVE_AFTER = 120
_DECIDED_AFTER = 15_000
#: Wide enough to absorb the gap between seeding and querying, narrow enough
#: that the run is plainly on one side of the threshold.
_MARGIN = 20
#: Far above anything a single test seeds, so ordering and the default page
#: size never hide a run from the answer.
_EVERYTHING = 1_000

_ALL_STATUSES = tuple(AgentRunStatus)


@dataclass(frozen=True, slots=True)
class _SeededRun:
    id: UUID
    status: AgentRunStatus
    age_seconds: int


async def _run_aged(
    scenario: E2EScenario,
    *,
    status: AgentRunStatus,
    started_ago: int,
    touched_ago: int = 0,
) -> _SeededRun:
    """A run in its own conversation -- one active run per conversation is a
    unique index -- with ``started_at`` and ``updated_at`` set independently,
    so a query keyed on the wrong column cannot pass by accident."""
    run_id = await running_run(await seeded_conversation(scenario))
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        await session.execute(
            update(AgentRunModel)
            .where(AgentRunModel.id == run_id)
            .values(
                status=status.value,
                started_at=now - timedelta(seconds=started_ago),
                updated_at=now - timedelta(seconds=touched_ago),
            )
        )
        await session.commit()
    return _SeededRun(id=run_id, status=status, age_seconds=started_ago)


async def _stale() -> set[UUID]:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        rows = await ConversationRepository(uow).list_stale_active_runs(
            cutoff_seconds=_DECIDED_AFTER, limit=_EVERYTHING
        )
    return {row.id for row in rows}


async def _pending_liveness() -> set[UUID]:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        rows = await ConversationRepository(uow).list_active_runs_pending_liveness(
            cutoff_seconds=_UNRESPONSIVE_AFTER,
            decided_after_seconds=_DECIDED_AFTER,
            limit=_EVERYTHING,
        )
    return {row.id for row in rows}


async def _stuck_stopping() -> set[UUID]:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        rows = await ConversationRepository(uow).list_runs_stuck_stopping(
            cutoff_seconds=_UNRESPONSIVE_AFTER, limit=_EVERYTHING
        )
    return {row.id for row in rows}


#: Every band the two age queries distinguish, with each edge approached from
#: both sides.
_AGES = (
    _UNRESPONSIVE_AFTER - _MARGIN,  # young: nobody may judge it yet
    _UNRESPONSIVE_AFTER + _MARGIN,  # just old enough to ask about liveness
    _DECIDED_AFTER - _MARGIN,  # the last moment liveness still decides
    _DECIDED_AFTER + _MARGIN,  # age alone decides from here
    _DECIDED_AFTER * 4,  # long dead
)


async def test_every_active_run_is_decided_by_exactly_the_query_its_age_selects(
    scenario,
) -> None:
    seeded = [
        await _run_aged(scenario, status=status, started_ago=age)
        for status in _ALL_STATUSES
        for age in _AGES
    ]

    stale = await _stale()
    pending = await _pending_liveness()

    for run in seeded:
        active = run.status in ACTIVE_AGENT_RUN_STATUSES
        expect_stale = active and run.age_seconds > _DECIDED_AFTER
        expect_pending = (
            active and _UNRESPONSIVE_AFTER < run.age_seconds < _DECIDED_AFTER
        )
        where = f"{run.status.value} run started {run.age_seconds}s ago"
        assert (run.id in stale) is expect_stale, f"stale answer wrong for a {where}"
        assert (run.id in pending) is expect_pending, (
            f"liveness answer wrong for a {where}"
        )
    assert not stale & pending, "a run was offered to both deciders"


async def test_a_young_live_run_is_never_a_sweep_candidate(scenario) -> None:
    """The costly mistake in this direction: a run a minute old is working,
    and failing it destroys the answer the person is waiting for."""
    young = await _run_aged(
        scenario, status=AgentRunStatus.RUNNING, started_ago=_MARGIN
    )

    assert young.id not in await _stale()
    assert young.id not in await _pending_liveness()


@pytest.mark.parametrize("status", _ALL_STATUSES, ids=lambda s: s.value)
async def test_only_a_stop_ignored_past_the_cutoff_is_stuck(
    scenario, status: AgentRunStatus
) -> None:
    """Eligibility is STOP_REQUESTED alone, measured from when the stop was
    written. Every run here *started* long ago, so a query keyed on
    ``started_at`` would select the fresh stop too."""
    fresh = await _run_aged(
        scenario,
        status=status,
        started_ago=_DECIDED_AFTER * 4,
        touched_ago=_UNRESPONSIVE_AFTER - _MARGIN,
    )
    ignored = await _run_aged(
        scenario,
        status=status,
        started_ago=_DECIDED_AFTER * 4,
        touched_ago=_UNRESPONSIVE_AFTER + _MARGIN,
    )

    stuck = await _stuck_stopping()

    assert fresh.id not in stuck, "a stop the worker has just received is not stuck"
    assert (ignored.id in stuck) is (status is AgentRunStatus.STOP_REQUESTED)
