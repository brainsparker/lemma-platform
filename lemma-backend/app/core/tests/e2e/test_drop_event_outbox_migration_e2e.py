"""Migration 0063 drops the old transport's tables -- only once they are drained.

A fresh install (nothing ever reached the outbox) drops them and removes the
converter items 0058-0062 queued. An upgraded install with an owed row left,
or a converter still queued, is refused; ``LEMMA_CUTOVER_ACCEPT_LOSS=1``
overrides; the downgrade recreates both tables empty.
"""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[4]
BEFORE = "0062_pod_bundle_work"
REVISION = "0063_drop_event_outbox"
_OWED_ROW = (
    "INSERT INTO domain_event_outbox (id, stream, event_type, producer, payload, "
    "occurred_at, available_at, created_at) VALUES "
    "(%s, 'surface_events', 'surface.webhook.received', 'test', '{}', now(), "
    "now(), now())"
)


def _tables(connection: psycopg.Connection) -> tuple[object, ...]:
    row = connection.execute(
        "SELECT to_regclass('domain_event_outbox'), to_regclass('domain_event_inbox'), "
        "(SELECT count(*) FROM work_items WHERE kind IN ('changes.drain', "
        "'surfaces.drain_events', 'workflow.drain_schedule_fires'))"
    ).fetchone()
    assert row is not None
    return tuple(row)


def test_the_outbox_is_dropped_only_when_nothing_is_owed() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")

        def migrate(direction: str, revision: str, **env: str):
            return subprocess.run(
                ["uv", "run", "--no-sync", "alembic", direction, revision],
                cwd=BACKEND,
                env={**os.environ, "DATABASE_URL": database_url, **env},
                capture_output=True,
                text=True,
                timeout=240,
            )

        first = migrate("upgrade", BEFORE)
        assert first.returncode == 0, first.stdout[-3000:] + first.stderr[-3000:]
        with psycopg.connect(sync_url) as connection:
            # The converters were queued by 0058, 0061 and 0062.
            assert _tables(connection)[2] == 3

        # Fresh: nothing ever reached the outbox.
        fresh = migrate("upgrade", REVISION)
        assert fresh.returncode == 0, fresh.stdout + fresh.stderr
        with psycopg.connect(sync_url) as connection:
            assert _tables(connection) == (None, None, 0)

        # The downgrade recreates them empty; then an owed row is left.
        assert migrate("downgrade", BEFORE).returncode == 0
        with psycopg.connect(sync_url, autocommit=True) as connection:
            assert _tables(connection)[:2] == (
                "domain_event_outbox",
                "domain_event_inbox",
            )
            connection.execute(_OWED_ROW, (uuid4(),))
        refused = migrate("upgrade", REVISION)
        assert refused.returncode != 0
        assert "not drained" in refused.stdout + refused.stderr
        with psycopg.connect(sync_url) as connection:
            assert _tables(connection)[0] == "domain_event_outbox"

        # A converter still queued is refused too, even with the row published.
        with psycopg.connect(sync_url, autocommit=True) as connection:
            connection.execute("UPDATE domain_event_outbox SET published_at = now()")
            connection.execute(
                "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
                "VALUES (gen_random_uuid(), 'system', 'admin', 'surfaces.drain_events', "
                "'{}', 'surfaces-drain-events', now())"
            )
        assert migrate("upgrade", REVISION).returncode != 0

        # The operator's override.
        accepted = migrate("upgrade", REVISION, LEMMA_CUTOVER_ACCEPT_LOSS="1")
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr
        with psycopg.connect(sync_url) as connection:
            assert _tables(connection)[:2] == (None, None)
