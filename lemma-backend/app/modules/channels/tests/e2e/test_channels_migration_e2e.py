"""Migration 0058 replaces conversation links and group tables with channels.

Groups and web chat were never released, and a member's private chat starts
afresh in their direct channel, so nothing is carried over: the upgrade drops
the link and group tables and makes the channel ones, and the downgrade makes
the old tables again, empty. What is checked is the schema each way -- the
index that keeps one conversation per person, agent and channel, and the
columns and tables the channel app will build on without a migration of its
own.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0057_contacts_for_everyone"
AFTER = "0058_channels"

_OLD = (
    "agent_surface_conversation_links",
    "agent_surface_groups",
    "agent_surface_group_messages",
    "notifications",
)
_NEW = (
    "external_accounts",
    "channels",
    "channel_members",
    "channel_read_state",
    "channel_sources",
    "channel_messages",
    "channel_mentions",
    "channel_questions",
    "channel_message_deliveries",
)


def test_channels_replace_links_and_groups_and_roll_back_cleanly() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        environment = {**os.environ, "DATABASE_URL": database_url}
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")

        def migrate(direction: str, revision: str) -> None:
            result = subprocess.run(
                ["uv", "run", "--no-sync", "alembic", direction, revision],
                cwd=BACKEND,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert result.returncode == 0, result.stdout + result.stderr

        def present(*tables: str) -> set[str]:
            with psycopg.connect(plain_url) as connection:
                return {
                    table
                    for table in tables
                    if connection.execute("SELECT to_regclass(%s)", [table]).fetchone()
                    != (None,)
                }

        def columns(table: str) -> set[str]:
            with psycopg.connect(plain_url) as connection:
                rows = connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = %s",
                    [table],
                ).fetchall()
            return {row[0] for row in rows}

        def index(name: str) -> str | None:
            with psycopg.connect(plain_url) as connection:
                row = connection.execute(
                    "SELECT indexdef FROM pg_indexes WHERE indexname = %s", [name]
                ).fetchone()
            return row[0] if row else None

        migrate("upgrade", BEFORE)
        assert present(*_OLD) == set(_OLD)
        assert not present(*_NEW)

        migrate("upgrade", AFTER)
        assert not present(*_OLD), "the link and group tables outlived channels"
        assert present(*_NEW) == set(_NEW)
        # A place belongs to an account; a group's bot-side settings and a
        # place's address live on its source.
        assert {
            "account_id",
            "is_private",
            "delivery_ref",
            "owner_user_id",
            "answers_outsiders",
            "request_id",
        } <= columns("channel_sources")
        # Who may see a channel is its own column; a contact's inbox is
        # columns, not settings; the timeline's head is a number.
        assert {
            "visibility",
            "agents_answer",
            "hand_back_after_hours",
            "assignee_user_id",
            "status",
            "last_seq",
            "last_line_id",
            "last_line_at",
        } <= columns("channels")
        assert {
            "seq",
            "client_message_id",
            "parts",
            "search",
            "thread_root_id",
            "reply_to_id",
            "edited_at",
            "deleted_at",
        } <= columns("channel_messages")
        assert {"role", "notify", "notify_via", "muted_until", "answers"} <= columns(
            "channel_members"
        )
        assert {"ref", "kind", "state", "batch_id", "expires_at"} <= columns(
            "channel_questions"
        )
        assert index("uq_channel_messages_client") is not None
        assert index("ix_channel_mentions_user") is not None
        assert "channel_id" in columns("agent_conversations")
        one_each = index("uq_agent_conversations_channel_person")
        assert one_each is not None and "UNIQUE" in one_each, one_each
        assert index("uq_channel_sources_place") is not None

        migrate("downgrade", BEFORE)
        assert present(*_OLD) == set(_OLD)
        assert not present(*_NEW)
        assert "channel_id" not in columns("agent_conversations")

        migrate("upgrade", "head")
