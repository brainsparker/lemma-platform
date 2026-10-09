"""The deployment path for the one-message-per-send index: up, down, and up again."""

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
REVISION = "0044_agent_message_client_id"
INDEX = "uq_agent_message_client_message_id"


def test_the_client_message_id_index_upgrades_and_rolls_back_cleanly() -> None:
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

        def index(connection: psycopg.Connection) -> tuple[object, ...] | None:
            return connection.execute(
                "SELECT i.indisunique, pg_get_expr(i.indpred, i.indrelid) "
                "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = %s",
                (INDEX,),
            ).fetchone()

        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            live = index(connection)
            assert live is not None
            unique, predicate = live
            assert unique is True
            assert "client_message_id" in str(predicate)

        migrate("downgrade", BEFORE)
        with psycopg.connect(sync_url) as connection:
            assert index(connection) is None

        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert index(connection) is not None
