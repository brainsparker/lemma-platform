"""The deployment path for the run-resume columns: up, down, and up again."""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0043_surface_groups"
REVISION = "0044_agent_host_run_resume"
COLUMNS = ("consumed_stream_id", "credential_expires_at")


def test_run_resume_columns_upgrade_and_roll_back_cleanly() -> None:
    scripts = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert scripts.get_revision(REVISION).down_revision == BEFORE

    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        environment = {**os.environ, "DATABASE_URL": database_url}

        def migrate(direction: str, revision: str) -> None:
            result = subprocess.run(
                ["uv", "run", "--no-sync", "alembic", direction, revision],
                cwd=BACKEND,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert result.returncode == 0, result.stdout + result.stderr

        def columns(connection: psycopg.Connection) -> set[str]:
            rows = connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'agent_host_run_leases'"
            ).fetchall()
            return {row[0] for row in rows} & set(COLUMNS)

        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert columns(connection) == set(COLUMNS)

        migrate("downgrade", BEFORE)
        with psycopg.connect(sync_url) as connection:
            assert columns(connection) == set()

        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert columns(connection) == set(COLUMNS)
