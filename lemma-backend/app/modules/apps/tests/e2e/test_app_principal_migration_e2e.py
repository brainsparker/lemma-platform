"""Migration 0063 gives every existing app the grants it starts its life with.

An app used to be its viewer (audit H20); from 0062 it is its own principal,
and holds only what it is granted. There is no record of what an app called --
request logs and product analytics live outside the database, and an app's
bundle lives in object storage -- so the grants cannot be read off its usage.
The migration gives each app reads of the pod data any member may read: the
pod's POD and PUBLIC tables (schema and rows), its POD and PUBLIC top-level
folders and files, and the pod itself (reading it, listing its tables). Nothing else -- no writes, no deletes, no connector, no
RESTRICTED or PERSONAL resource -- so the viewer's full authority is never
carried over, and the first call outside those grants is a typed
``APP_NOT_GRANTED`` an editor fixes by granting it.

Seeded before it, in one pod: an app, tables of every visibility, top-level
folders and a nested file, and an agent's grant that must not move. The
downgrade removes every grant to an app, and the upgrade runs again.
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
BEFORE = "0062_usage_counter_basis"
REVISION = "0063_pod_connectors"
READ_ROWS = {"datastore.table.read", "datastore.record.read"}


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
        for name in (
            "org",
            "owner",
            "pod",
            "app",
            "agent",
            "pod_table",
            "public_table",
            "restricted_table",
            "personal_table",
            "reports",
            "hr",
            "nested",
            "readme",
        )
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
            (ids["owner"], f"{ids['owner'].hex[:8]}@example.com"),
        )
        run(
            "INSERT INTO pods (id, user_id, organization_id, name, config, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'Pod', '{}', false, now(), now())",
            (ids["pod"], ids["owner"], ids["org"]),
        )
        run(
            "INSERT INTO apps (id, pod_id, user_id, name, public_slug, status, "
            "visibility, created_at, updated_at) VALUES "
            "(%s, %s, %s, 'dashboard', %s, 'READY', 'PUBLIC', now(), now())",
            (ids["app"], ids["pod"], ids["owner"], f"dash-{ids['app'].hex[:8]}"),
        )
        for table, visibility in (
            ("pod_table", "POD"),
            ("public_table", "PUBLIC"),
            ("restricted_table", "RESTRICTED"),
            ("personal_table", "PERSONAL"),
        ):
            run(
                "INSERT INTO datastore_tables (id, pod_id, user_id, table_name, "
                "primary_key_column, columns, enable_rls, visibility, "
                "contact_owned, created_at, updated_at) VALUES "
                "(%s, %s, %s, %s, 'id', '[]', false, %s, false, now(), now())",
                (ids[table], ids["pod"], ids["owner"], table, visibility),
            )
        for row, kind, path, visibility, parent in (
            ("reports", "FOLDER", "/reports", "POD", None),
            ("hr", "FOLDER", "/hr", "RESTRICTED", None),
            ("nested", "FILE", "/reports/q1.md", "POD", "reports"),
            ("readme", "FILE", "/readme.md", "PUBLIC", None),
        ):
            run(
                "INSERT INTO datastore_files (id, pod_id, owner_user_id, kind, "
                "visibility, path, name, size_bytes, search_enabled, status, "
                "processing_attempts, processing_claim, storage_layout, "
                "parent_id, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, "
                "%s, %s, 0, true, 'NOT_REQUIRED', 0, 0, 'path', %s, now(), now())",
                (
                    ids[row],
                    ids["pod"],
                    ids["owner"],
                    kind,
                    visibility,
                    path,
                    path.rsplit("/", 1)[-1],
                    ids[parent] if parent else None,
                ),
            )
        # The permission catalogue is seeded by the API at boot, not by the
        # chain, so a fresh database has none of the rows the grants point at.
        for permission, resource_type in (
            ("datastore.table.read", "datastore_table"),
            ("datastore.record.read", "datastore_table"),
            ("datastore.table.update", "datastore_table"),
            ("folder.read", "folder"),
            ("pod.read", "pod"),
        ):
            run(
                "INSERT INTO auth_permissions (id, scope, resource_type, "
                "description, system_only, created_at, updated_at) VALUES "
                "(%s, 'POD', %s, 'x', false, now(), now()) ON CONFLICT DO NOTHING",
                (permission, resource_type),
            )
        # An agent's grant, which the app's section must leave alone.
        run(
            "INSERT INTO resource_permission_grants (id, pod_id, resource_type, "
            "resource_id, grantee_type, grantee_id, permission_id, created_at) "
            "VALUES (%s, %s, 'datastore_table', %s, 'AGENT', %s, "
            "'datastore.table.update', now())",
            (uuid4(), ids["pod"], ids["restricted_table"], ids["agent"]),
        )
    return ids


def _grants(plain_url: str, grantee_type: str) -> set[tuple[str, UUID, str]]:
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            "SELECT resource_type, resource_id, permission_id, grantee_id "
            "FROM resource_permission_grants WHERE grantee_type = %s",
            (grantee_type,),
        ).fetchall()
    return {(row[0], row[1], row[2]) for row in rows}


def test_each_app_gets_reads_of_what_every_member_reads_and_nothing_more() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)
        agent_before = _grants(plain_url, "AGENT")

        _migrate(database_url, "upgrade", REVISION)
        granted = _grants(plain_url, "APP")

        expected = {
            ("datastore_table", ids[table], permission)
            for table in ("pod_table", "public_table")
            for permission in READ_ROWS
        } | {
            ("folder", ids["reports"], "folder.read"),
            ("document", ids["readme"], "folder.read"),
            ("pod", ids["pod"], "pod.read"),
            ("pod", ids["pod"], "datastore.table.read"),
        }
        assert granted == expected
        # Never a write, a delete or a connector; never RESTRICTED or PERSONAL
        # rows, nor the nested file, which its folder's grant already covers.
        assert _grants(plain_url, "AGENT") == agent_before

        _migrate(database_url, "downgrade", BEFORE)
        assert _grants(plain_url, "APP") == set()
        assert _grants(plain_url, "AGENT") == agent_before

        _migrate(database_url, "upgrade", REVISION)
        assert _grants(plain_url, "APP") == expected
