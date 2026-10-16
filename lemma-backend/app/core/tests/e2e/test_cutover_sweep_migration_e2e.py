"""Migration 0056 queues the cutover safely, in one upgrade from any earlier release.

* A database the old transport left with owed deliveries upgrades from the
  last release before this stack (0052) through 0056 without refusing and
  without dropping anything: both tables stay, the cutover sweep is queued
  once, and the converter items of the earlier design are gone.
* Taking over (what the sweep does every pass) writes exactly one
  ``cutover.legacy_event`` per owed (consumer, event) -- unpublished rows and
  published rows whose consumer never settled them -- and nothing for a
  settled, dead-lettered or live-claimed delivery, or one the release before
  this marked as already done. It settles each consumer's inbox row, so a
  second pass (or an old worker) finds nothing.
* A fresh database upgrades with both tables kept (0056 is expand-only; 0057
  drops them), its sweep queued and the earlier design's converter gone; the
  downgrade removes the sweep and leaves the tables, and upgrading again works.
"""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.infrastructure.cutover.routes import ROUTES
from app.core.infrastructure.cutover.takeover import count_owed, take_over
from app.core.infrastructure.cutover.work import last_activity
from app.core.test_utils import get_postgres_container, get_postgres_url
from app.modules.test_support.e2e.legacy_transport import (
    event,
    inbox_row,
    outbox_row,
)

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[4]
BEFORE_STACK = "0052_workflow_run_times_utc"
BEFORE = "0055_change_log_and_flows"
REVISION = "0056_agent_surface_bundle_work"

#: Routes whose ``where`` reads domain rows (record events): exercised in the
#: datastore cutover e2e, against real pods and triggers.
_NEEDS_STATE = {route.consumer for route in ROUTES if route.where}


def _migrate(database_url: str, direction: str, revision: str) -> None:
    result = subprocess.run(
        ["uv", "run", "--no-sync", "alembic", direction, revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


def _state(sync_url: str) -> tuple[object, ...]:
    with psycopg.connect(sync_url) as connection:
        row = connection.execute(
            "SELECT to_regclass('domain_event_outbox')::text, "
            "to_regclass('domain_event_inbox')::text, "
            "(SELECT count(*) FROM work_items WHERE kind = 'cutover.legacy_sweep'), "
            "(SELECT count(*) FROM work_items WHERE kind IN ('changes.drain', "
            "'surfaces.drain_events', 'workflow.drain_schedule_fires'))"
        ).fetchone()
    assert row is not None
    return tuple(row)


@pytest.mark.asyncio
async def test_an_install_with_owed_deliveries_upgrades_in_one_step() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE_STACK)

        engine = create_async_engine(database_url)
        #: (event type, dead, {consumer: inbox status}) per row written.
        written: list[tuple[str, bool, dict[str, str]]] = []
        old = datetime.now(UTC) - timedelta(hours=1)
        try:
            async with engine.begin() as conn:

                async def row(
                    event_type: str,
                    *,
                    published: bool = False,
                    dead: bool = False,
                    inbox: tuple[str, str] | None = None,
                    mark: str | None = None,
                ) -> None:
                    row_id = await outbox_row(
                        conn,
                        event(event_type, pod_id=uuid4()),
                        occurred_at=old,
                        published=published,
                        dead=dead,
                    )
                    statuses: dict[str, str] = {}
                    if inbox is not None:
                        consumer, status = inbox
                        await inbox_row(
                            conn, consumer, row_id, event_type, status=status, mark=mark
                        )
                        statuses[consumer] = "MARKED" if mark else status
                    written.append((event_type, dead, statuses))

                for route in ROUTES:
                    if route.consumer in _NEEDS_STATE:
                        continue
                    event_type = route.types[0]
                    mine = route.consumer
                    # Owed: never published; published and never consumed; a
                    # consumer that failed and was going to retry.
                    await row(event_type)
                    await row(event_type, published=True)
                    await row(event_type, published=True, inbox=(mine, "RETRYING"))
                    # Not owed to this consumer: settled, dead-lettered in the
                    # outbox, claimed by a live old worker, or marked done by
                    # the release before.
                    for status in ("COMPLETED", "TERMINAL", "DEAD_LETTER"):
                        await row(event_type, published=True, inbox=(mine, status))
                    await row(event_type, dead=True)
                    await row(event_type, published=True, inbox=(mine, "PROCESSING"))
                    await row(
                        event_type,
                        published=True,
                        inbox=(mine, "COMPLETED"),
                        mark="work_runtime",
                    )
                # A type no route owes: its work is seeded from state.
                await row("agent.run.started")
        finally:
            await engine.dispose()

        # Several consumers read one type (pod.deleted, agent.run.completed):
        # a row is owed to every one of them that has not settled it.
        expected: Counter[str] = Counter()
        live_claims = 0
        for event_type, dead, statuses in written:
            for route in ROUTES:
                if route.consumer in _NEEDS_STATE or event_type not in route.types:
                    continue
                status = statuses.get(route.consumer)
                live_claims += status == "PROCESSING"
                if not dead and status in (None, "RETRYING"):
                    expected[route.consumer] += 1

        _migrate(database_url, "upgrade", REVISION)
        assert _state(sync_url) == ("domain_event_outbox", "domain_event_inbox", 1, 0)
        with psycopg.connect(sync_url) as connection:
            seeded = connection.execute(
                "SELECT (payload->>'horizon')::timestamptz FROM work_items "
                "WHERE kind = 'cutover.legacy_sweep'"
            ).fetchone()
            assert seeded is not None and seeded[0] is not None, (
                "the sweep carries the release's horizon from its first pass"
            )

        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as conn:
                session = AsyncSession(bind=conn)
                taken = await take_over(session)
                assert await last_activity(session) is not None
            assert Counter(taken) == expected
            async with engine.begin() as conn:
                assert await take_over(AsyncSession(bind=conn)) == {}, (
                    "the second pass finds nothing"
                )
            # What the previous release writes meanwhile is found by a pass
            # that reads only from its watermark on.
            async with engine.begin() as conn:
                await outbox_row(conn, event("pod.join_request.requested"))
                await outbox_row(conn, event("pod.member.removed"), occurred_at=old)
            async with engine.begin() as conn:
                since = datetime.now(UTC) - timedelta(minutes=5)
                assert await take_over(AsyncSession(bind=conn), since=since) == {
                    "pod.join-request-email": 1
                }
            async with engine.begin() as conn:
                # The final pass reads everything.
                assert await take_over(AsyncSession(bind=conn)) == {
                    "agent-surfaces.pod-member-removed": 1
                }
            expected["pod.join-request-email"] += 1
            expected["agent-surfaces.pod-member-removed"] += 1

            # Past the release's horizon nothing is replayed: an owed row from
            # long before it is left, and is not counted as owed either.
            horizon = datetime.now(UTC) - timedelta(minutes=30)
            async with engine.begin() as conn:
                await outbox_row(
                    conn,
                    event("pod.join_request.requested"),
                    occurred_at=datetime.now(UTC) - timedelta(hours=2),
                )
                await outbox_row(conn, event("pod.join_request.requested"))
            async with engine.begin() as conn:
                assert await take_over(AsyncSession(bind=conn), horizon=horizon) == {
                    "pod.join-request-email": 1
                }
            async with engine.begin() as conn:
                route = next(
                    r for r in ROUTES if r.consumer == "pod.join-request-email"
                )
                session = AsyncSession(bind=conn)
                assert await count_owed(session, route, horizon=horizon) == 0
                assert await count_owed(session, route) == 1, (
                    "the old row is still there"
                )
            expected["pod.join-request-email"] += 1
        finally:
            await engine.dispose()

        with psycopg.connect(sync_url) as connection:
            items = connection.execute(
                "SELECT payload->>'route', count(*), count(DISTINCT dedup_key) "
                "FROM work_items WHERE kind = 'cutover.legacy_event' GROUP BY 1"
            ).fetchall()
            assert {route: n for route, n, _ in items} == dict(expected)
            assert all(n == distinct for _, n, distinct in items)
            # Every taken delivery is settled for its consumer: an old worker
            # still running skips it.
            settled = connection.execute(
                "SELECT count(*) FROM domain_event_inbox "
                "WHERE last_error_type = 'work_runtime_cutover' AND status = 'COMPLETED'"
            ).fetchone()
            assert settled is not None and settled[0] == sum(expected.values())
            # The live claims were left to their owners.
            live = connection.execute(
                "SELECT count(*) FROM domain_event_inbox WHERE status = 'PROCESSING'"
            ).fetchone()
            assert live is not None and live[0] == live_claims


def test_a_fresh_install_keeps_the_tables_and_the_downgrade_works() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        with psycopg.connect(sync_url) as connection:
            # 0055 queued the earlier design's converter; 0056 retires it.
            row = connection.execute(
                "SELECT count(*) FROM work_items WHERE kind = 'changes.drain'"
            ).fetchone()
            assert row is not None and row[0] == 1

        tables = ("domain_event_outbox", "domain_event_inbox")
        _migrate(database_url, "upgrade", REVISION)
        assert _state(sync_url) == (*tables, 1, 0)
        assert _index(sync_url) == "ix_domain_event_outbox_cutover"

        _migrate(database_url, "downgrade", BEFORE)
        assert _state(sync_url)[:3] == (*tables, 0)
        assert _index(sync_url) is None

        _migrate(database_url, "upgrade", REVISION)
        assert _state(sync_url) == (*tables, 1, 0)
        # Idempotent: a rerun over what is there trips over nothing.
        _migrate(database_url, "downgrade", BEFORE)
        _migrate(database_url, "upgrade", REVISION)
        assert _state(sync_url) == (*tables, 1, 0)


def _index(sync_url: str) -> object:
    with psycopg.connect(sync_url) as connection:
        row = connection.execute(
            "SELECT to_regclass('ix_domain_event_outbox_cutover')::text"
        ).fetchone()
    assert row is not None
    return row[0]
