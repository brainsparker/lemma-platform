"""Migration 0057 drops the old columns and never the old transport's tables.

* A fresh install (both tables empty) upgrades with both tables kept -- a
  previous release still running could write them, so dropping them is
  ``finish``'s job -- and the agent waits' ``fire_lease_until`` /
  ``wake_attempts`` and ``schedule_runs.last_inspected_at`` with its index
  dropped; the downgrade restores the columns and the index, recreates the
  tables empty where ``finish`` had dropped them, and upgrading again works.
* An install whose outbox still holds rows upgrades the same way, keeping both
  tables for ``app.cutover_finish``; rerunning it changes nothing.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.test_utils import get_postgres_container, get_postgres_url
from app.modules.test_support.e2e.legacy_transport import event, outbox_row

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[4]
BEFORE = "0056_agent_surface_bundle_work"
REVISION = "0057_drop_old_transport"

_TABLES = ("domain_event_outbox", "domain_event_inbox")


def _migrate(database_url: str, direction: str, revision: str) -> str:
    result = subprocess.run(
        ["uv", "run", "--no-sync", "alembic", direction, revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return result.stdout + result.stderr


def _state(sync_url: str) -> dict[str, object]:
    with psycopg.connect(sync_url) as connection:
        row = connection.execute(
            "SELECT to_regclass('domain_event_outbox')::text, "
            "to_regclass('domain_event_inbox')::text, "
            "to_regclass('ix_schedule_runs_recoverable')::text, "
            "(SELECT string_agg(table_name || '.' || column_name, ',' "
            "ORDER BY table_name, column_name) FROM information_schema.columns "
            "WHERE (table_name = 'agent_conversation_waits' AND column_name IN "
            "('fire_lease_until', 'wake_attempts')) OR (table_name = "
            "'schedule_runs' AND column_name = 'last_inspected_at'))"
        ).fetchone()
    assert row is not None
    return {
        "tables": tuple(name for name in row[:2] if name),
        "index": row[2],
        "columns": row[3],
    }


_OLD_COLUMNS = (
    "agent_conversation_waits.fire_lease_until,"
    "agent_conversation_waits.wake_attempts,"
    "schedule_runs.last_inspected_at"
)
_UPGRADED = {"tables": _TABLES, "index": None, "columns": None}
_KEPT_BY_0056 = {
    "tables": _TABLES,
    "index": "ix_schedule_runs_recoverable",
    "columns": _OLD_COLUMNS,
}


def test_a_fresh_install_keeps_the_tables_and_downgrades_after_finish() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        assert _state(sync_url) == _KEPT_BY_0056, "0056 is expand-only"

        _migrate(database_url, "upgrade", REVISION)
        assert _state(sync_url) == _UPGRADED, "empty is no reason to drop them"

        # ``finish`` has run: the downgrade recreates what it dropped.
        with psycopg.connect(sync_url) as connection:
            connection.execute("DROP TABLE domain_event_inbox, domain_event_outbox")
        _migrate(database_url, "downgrade", BEFORE)
        assert _state(sync_url) == _KEPT_BY_0056
        with psycopg.connect(sync_url) as connection:
            row = connection.execute(
                "SELECT (SELECT count(*) FROM domain_event_outbox), "
                "(SELECT count(*) FROM domain_event_inbox), "
                "to_regclass('ix_domain_event_outbox_cutover') IS NOT NULL"
            ).fetchone()
        assert row == (0, 0, True), "recreated empty, as the sweep reads them"

        _migrate(database_url, "upgrade", REVISION)
        assert _state(sync_url) == _UPGRADED


@pytest.mark.asyncio
async def test_an_install_still_holding_rows_keeps_the_tables() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as conn:
                await outbox_row(conn, event("pod.member.removed", pod_id=uuid4()))
        finally:
            await engine.dispose()

        output = _migrate(database_url, "upgrade", REVISION)
        assert "app.cutover_finish" in output, output[-2000:]
        assert _state(sync_url) == _UPGRADED

        # Idempotent both ways: the half that ran is not tripped over.
        _migrate(database_url, "downgrade", BEFORE)
        assert _state(sync_url) == _KEPT_BY_0056
        _migrate(database_url, "upgrade", REVISION)
        assert _state(sync_url) == _UPGRADED
