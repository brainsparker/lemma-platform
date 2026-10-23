"""Migration 0063, its last three steps: record deletes become their own
permission, Lemma-made Slack apps are marked as such on the surfaces bound
before the flag existed, and the public links a named agent or function minted
are deleted along with the column that recorded it (audit M22).

Seeded before it, in one pod: the system role POD_USER and a custom role, both
holding `datastore.record.write`, and that permission granted on a table to a
member, a role, an agent and another pod; then two Slack surfaces bound to a
member's account, one connected through an install Lemma made (named after the
app id) and one through an install a person brought (named after the client
id). After it: both roles and every grant but the pod's carry
`datastore.record.delete` too, and only the Lemma-made surface -- now on a pod
connector -- says `app_made_by_lemma`. Then two public links to a file in the
pod, one a person minted and one an agent minted for them: after it only the
person's is left, and `minted_by_workload` is gone. The downgrade takes the
first two back and the column with them, empty; the agent's link stays deleted.
"""

from __future__ import annotations

import json
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

BACKEND = Path(__file__).resolve().parents[4]
BEFORE = "0062_usage_counter_basis"
REVISION = "0063_pod_connectors"
WRITE = "datastore.record.write"
DELETE = "datastore.record.delete"
GRANTEES = ("POD_MEMBER", "ROLE", "AGENT", "POD")


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
            "user",
            "pod",
            "lemma_agent",
            "own_agent",
            "table",
            "system_role",
            "custom_role",
            "lemma_install",
            "own_install",
            "lemma_account",
            "own_account",
            "lemma_surface",
            "own_surface",
            "person_link",
            "agent_link",
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
            (ids["user"], f"{ids['user'].hex[:8]}@example.com"),
        )
        run(
            "INSERT INTO pods (id, user_id, organization_id, name, config, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'Pod', '{}', false, now(), now())",
            (ids["pod"], ids["user"], ids["org"]),
        )
        # A pod member: the surfaces are bound to their accounts.
        org_member = uuid4()
        run(
            "INSERT INTO organization_members (id, organization_id, user_id, "
            "role, created_at, updated_at) VALUES "
            "(%s, %s, %s, 'ORG_MEMBER', now(), now())",
            (org_member, ids["org"], ids["user"]),
        )
        run(
            "INSERT INTO pod_members (id, pod_id, organization_member_id, "
            "created_at, updated_at) VALUES (%s, %s, %s, now(), now())",
            (uuid4(), ids["pod"], org_member),
        )
        # Each surface has its own agent: one Slack surface per agent.
        for agent in ("lemma_agent", "own_agent"):
            run(
                "INSERT INTO agents (id, pod_id, user_id, name, kind, visibility, "
                "instruction, toolsets, created_at, updated_at) VALUES "
                "(%s, %s, %s, %s, 'USER', 'POD', 'Help.', '[]', now(), now())",
                (ids[agent], ids["pod"], ids["user"], agent),
            )
        run(
            "INSERT INTO auth_permissions (id, scope, resource_type, description, "
            "system_only, created_at, updated_at) VALUES (%s, 'POD', "
            "'datastore_record', 'x', false, now(), now()) ON CONFLICT DO NOTHING",
            (WRITE,),
        )
        for role, name, is_system in (
            ("system_role", "POD_USER", True),
            ("custom_role", "RECORD_KEEPER", False),
        ):
            run(
                "INSERT INTO roles (id, organization_id, pod_id, name, is_system, "
                "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, now(), now())",
                (ids[role], ids["org"], ids["pod"], name, is_system),
            )
            run(
                "INSERT INTO role_permissions (id, role_id, permission_id, "
                "created_at) VALUES (%s, %s, %s, now())",
                (uuid4(), ids[role], WRITE),
            )
        for grantee in GRANTEES:
            run(
                "INSERT INTO resource_permission_grants (id, pod_id, "
                "resource_type, resource_id, grantee_type, grantee_id, "
                "permission_id, created_by_user_id, created_at) VALUES "
                "(%s, %s, 'datastore_table', %s, %s, %s, %s, %s, now())",
                (
                    uuid4(),
                    ids["pod"],
                    ids["table"],
                    grantee,
                    uuid4(),
                    WRITE,
                    ids["user"],
                ),
            )

        run(
            "INSERT INTO connectors (id, title, description, kinds, is_active, "
            "created_at, updated_at) VALUES ('slack', 'Slack', 'chat', '[]', "
            "true, now(), now()) ON CONFLICT DO NOTHING"
        )
        # The two names the two code paths write: the app id for an app Lemma
        # made, the client id for one a person brought.
        for kind, name in (
            ("lemma", "Helper (A0ABCDEF12)"),
            ("own", "helper (1234567.8901234)"),
        ):
            install, account = f"{kind}_install", f"{kind}_account"
            surface, agent = f"{kind}_surface", f"{kind}_agent"
            run(
                "INSERT INTO auth_configs (id, organization_id, connector_id, name, "
                "kind, config_source, status, is_default, created_at, updated_at) "
                "VALUES (%s, %s, 'slack', %s, 'http', 'ORG_CUSTOM', 'ACTIVE', "
                "false, now(), now())",
                (ids[install], ids["org"], name),
            )
            run(
                "INSERT INTO accounts (id, user_id, organization_id, "
                "auth_config_id, connector_id, status, is_default, created_at, "
                "updated_at) VALUES (%s, %s, %s, %s, 'slack', 'CONNECTED', false, "
                "now(), now())",
                (ids[account], ids["user"], ids["org"], ids[install]),
            )
            run(
                "INSERT INTO agent_surfaces (id, pod_id, organization_id, agent_id, "
                "name, surface_type, event_mode, credential_mode, config, "
                "account_id, status, created_at, updated_at) VALUES "
                "(%s, %s, %s, %s, %s, 'SLACK', 'WEBHOOK', 'CUSTOM', %s, %s, "
                "'ACTIVE', now(), now())",
                (
                    ids[surface],
                    ids["pod"],
                    ids["org"],
                    ids[agent],
                    surface,
                    json.dumps({"slack": {"app_name": "crm"}}),
                    ids[account],
                ),
            )

        for link, workload in (
            ("person_link", None),
            ("agent_link", f"agent:{ids['own_agent']}"),
        ):
            run(
                "INSERT INTO datastore_signed_links (id, code, pod_id, "
                "created_by_user_id, minted_by_workload, path, object_key, "
                "content_type, filename, max_hits, expires_at, created_at, "
                "updated_at) VALUES (%s, %s, %s, %s, %s, '/notes.md', 'k', "
                "'text/markdown', 'notes.md', 5, now() + interval '1 day', "
                "now(), now())",
                (ids[link], link, ids["pod"], ids["user"], workload),
            )
    return ids


def _state(plain_url: str, ids: dict[str, UUID]) -> dict:
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        roles = run(
            "SELECT r.name FROM role_permissions rp JOIN roles r ON r.id = "
            "rp.role_id WHERE rp.permission_id = %s AND r.organization_id = %s",
            (DELETE, ids["org"]),
        ).fetchall()
        grants = run(
            "SELECT grantee_type FROM resource_permission_grants "
            "WHERE pod_id = %s AND permission_id = %s",
            (ids["pod"], DELETE),
        ).fetchall()
        surfaces = run(
            "SELECT id, config FROM agent_surfaces WHERE pod_id = %s", (ids["pod"],)
        ).fetchall()
        links = run(
            "SELECT code FROM datastore_signed_links WHERE pod_id = %s",
            (ids["pod"],),
        ).fetchall()
        link_columns = run(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'datastore_signed_links'"
        ).fetchall()
    return {
        "roles": {row[0] for row in roles},
        "grants": sorted(row[0] for row in grants),
        "surfaces": {row[0]: row[1] for row in surfaces},
        "links": sorted(row[0] for row in links),
        "workload_column": ("minted_by_workload",) in link_columns,
    }


def test_record_deletes_lemma_made_apps_and_agent_minted_links() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        after = _state(plain_url, ids)

        assert after["roles"] == {"POD_USER", "RECORD_KEEPER"}
        # Everyone who could delete through the write still can; another pod
        # never could, and is not given it.
        assert after["grants"] == ["AGENT", "POD_MEMBER", "ROLE"]
        assert after["surfaces"][ids["lemma_surface"]] == {
            "slack": {"app_name": "crm", "app_made_by_lemma": True}
        }
        assert after["surfaces"][ids["own_surface"]] == {"slack": {"app_name": "crm"}}
        # Only a person shares now; what an agent shared is not kept working.
        assert after["links"] == ["person_link"]
        assert after["workload_column"] is False

        _migrate(database_url, "downgrade", BEFORE)
        before = _state(plain_url, ids)
        with psycopg.connect(plain_url) as connection:
            gone = connection.execute(
                "SELECT count(*) FROM auth_permissions WHERE id = %s", (DELETE,)
            ).fetchone()
        assert gone == (0,)
        assert before["roles"] == set() and before["grants"] == []
        assert all(
            config == {"slack": {"app_name": "crm"}}
            for config in before["surfaces"].values()
        )
        # The column comes back; the deleted link, whose code was the
        # capability, does not.
        assert before["workload_column"] is True
        assert before["links"] == ["person_link"]

        # And again, from where the downgrade left it.
        _migrate(database_url, "upgrade", REVISION)
        assert _state(plain_url, ids) == after
