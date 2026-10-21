"""Migration 0061 gives every channel ``answers_shared``, true.

A group already there when it runs takes the default: from then on the
answers an agent gives a member there with their own access are read by
everybody in it. The downgrade drops the column, and upgrading again works.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0060_slack_apps"
REVISION = "0061_answers_shared"


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


def _a_group(plain_url: str) -> None:
    """A pod with one group, written before 0061."""
    org, owner, pod = uuid4(), uuid4(), uuid4()
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        run(
            "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
            "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
            (org, f"org-{org.hex[:8]}"),
        )
        run(
            "INSERT INTO users (id, email, is_verified, is_active, is_superuser, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, true, true, false, false, now(), now())",
            (owner, f"{owner.hex[:8]}@example.com"),
        )
        run(
            "INSERT INTO pods (id, user_id, organization_id, name, config, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'Pod', '{}', false, now(), now())",
            (pod, owner, org),
        )
        run(
            "INSERT INTO channels (id, pod_id, kind, visibility, title, "
            "created_at, updated_at) "
            "VALUES (%s, %s, 'GROUP', 'PUBLIC', 'launch', now(), now())",
            (uuid4(), pod),
        )


def _column(plain_url: str) -> tuple[object, ...] | None:
    with psycopg.connect(plain_url) as connection:
        return connection.execute(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'channels' AND column_name = 'answers_shared'"
        ).fetchone()


def _groups_shared(plain_url: str) -> list[bool]:
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            "SELECT answers_shared FROM channels WHERE kind = 'GROUP'"
        ).fetchall()
    return [row[0] for row in rows]


def test_every_group_shows_answers_after_it_and_it_rolls_back() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        assert _column(plain_url) is None
        _a_group(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        assert _column(plain_url) == ("boolean", "NO", "true")
        assert _groups_shared(plain_url) == [True], "the group there takes it on"

        _migrate(database_url, "downgrade", BEFORE)
        assert _column(plain_url) is None

        _migrate(database_url, "upgrade", REVISION)
        assert _column(plain_url) == ("boolean", "NO", "true")
        assert _groups_shared(plain_url) == [True]
