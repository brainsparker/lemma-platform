"""Migration 0064: the owner's "allow use inside sandboxes" on a pod account.

Seeded before it: one pod account, shared with the pod. After it: the account
is still there and does not allow sandbox use -- nobody has said it may -- and
a new row defaults the same way. The downgrade drops the column and leaves the
account alone; upgrading again gives the same answer.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

# A chain walk on its own Postgres, like the other migration tests: the nightly
# protected lane.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0063_pod_connectors"
REVISION = "0064_pod_account_sandbox_use"


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


def _seed(plain_url: str) -> dict[str, UUID]:
    ids = {
        name: uuid4()
        for name in ("org", "user", "pod", "install", "account", "pod_account")
    }
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        run(
            "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
            "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
            (ids["org"], f"org-{ids['org'].hex[:8]}"),
        )
        run(
            "INSERT INTO users (id, email, is_verified, is_active, is_superuser, "
            "is_deleted, created_at, updated_at) VALUES "
            "(%s, %s, true, true, false, false, now(), now())",
            (ids["user"], f"{ids['user'].hex[:8]}@example.com"),
        )
        run(
            "INSERT INTO pods (id, user_id, organization_id, name, config, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'Pod', '{}', false, now(), now())",
            (ids["pod"], ids["user"], ids["org"]),
        )
        run(
            "INSERT INTO connectors (id, title, description, kinds, is_active, "
            "created_at, updated_at) VALUES ('github', 'GitHub', 'code', '[]', "
            "true, now(), now()) ON CONFLICT DO NOTHING"
        )
        run(
            "INSERT INTO auth_configs (id, organization_id, connector_id, name, "
            "kind, config_source, status, is_default, created_at, updated_at) "
            "VALUES (%s, %s, 'github', 'GitHub', 'http', 'ORG_CUSTOM', 'ACTIVE', "
            "true, now(), now())",
            (ids["install"], ids["org"]),
        )
        run(
            "INSERT INTO accounts (id, user_id, organization_id, "
            "auth_config_id, connector_id, status, is_default, created_at, "
            "updated_at) VALUES (%s, %s, %s, %s, 'github', 'CONNECTED', true, "
            "now(), now())",
            (ids["account"], ids["user"], ids["org"], ids["install"]),
        )
        run(
            "INSERT INTO pod_accounts (id, pod_id, account_id, auth_config_id, "
            "owner_user_id, visibility, is_member_default, source, created_at, "
            "updated_at) VALUES (%s, %s, %s, %s, %s, 'POD', true, 'MANUAL', "
            "now(), now())",
            (
                ids["pod_account"],
                ids["pod"],
                ids["account"],
                ids["install"],
                ids["user"],
            ),
        )
    return ids


def _state(plain_url: str, ids: dict[str, UUID]) -> dict[str, object]:
    with psycopg.connect(plain_url) as connection:
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'pod_accounts'"
            ).fetchall()
        }
        has_column = "allow_sandbox_use" in columns
        allowed = (
            connection.execute(
                "SELECT allow_sandbox_use FROM pod_accounts WHERE id = %s",
                (ids["pod_account"],),
            ).fetchone()
            if has_column
            else None
        )
        visibility = connection.execute(
            "SELECT visibility FROM pod_accounts WHERE id = %s",
            (ids["pod_account"],),
        ).fetchone()
    return {"column": has_column, "allowed": allowed, "visibility": visibility}


def test_sandbox_use_starts_off_for_every_pod_account() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        after = _state(plain_url, ids)

        assert after == {"column": True, "allowed": (False,), "visibility": ("POD",)}

        _migrate(database_url, "downgrade", BEFORE)
        assert _state(plain_url, ids) == {
            "column": False,
            "allowed": None,
            "visibility": ("POD",),
        }

        # And again, from where the downgrade left it.
        _migrate(database_url, "upgrade", REVISION)
        assert _state(plain_url, ids) == after
