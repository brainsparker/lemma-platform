"""Migration 0057 turns every outsider into a contact without losing anybody.

Seeded at ``0056`` with one of each kind of person outside a pod as the old
model kept them -- a contact named by metadata, a group's strangers sharing
the ``~outsiders`` thread, an anonymous web visitor with a conversation -- and
a member's own conversation that must come through untouched.
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

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0056_agent_surface_bundle_work"
AFTER = "0057_contacts_for_everyone"


class _Pod:
    """One organization, one pod, its owner and one agent, as rows."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection
        self.organization_id = uuid4()
        self.pod_id = uuid4()
        self.owner_id = uuid4()
        self._insert(
            "organizations",
            id=self.organization_id,
            name="Acme",
            slug=f"acme-{self.organization_id.hex[:8]}",
            join_policy="INVITE",
        )
        self._insert(
            "users",
            id=self.owner_id,
            email=f"{self.owner_id.hex[:8]}@acme.example",
            is_verified=True,
            is_active=True,
            is_superuser=False,
            is_deleted=False,
        )
        self._insert(
            "pods",
            id=self.pod_id,
            user_id=self.owner_id,
            organization_id=self.organization_id,
            name="Sales",
            config=json.dumps({}),
            is_deleted=False,
        )
        # An agent of the pod's own, for its bot and its widget to answer as.
        self.agent_id = uuid4()
        self._insert(
            "agents",
            id=self.agent_id,
            pod_id=self.pod_id,
            user_id=self.owner_id,
            name="sales",
            visibility="POD",
            instruction="",
            toolsets=json.dumps([]),
        )

    def _insert(self, table: str, **values: object) -> None:
        columns = ", ".join(values)
        marks = ", ".join(["%s"] * len(values))
        self.connection.execute(
            f"INSERT INTO {table} ({columns}, created_at, updated_at) "
            f"VALUES ({marks}, now(), now())",
            [str(v) if isinstance(v, UUID) else v for v in values.values()],
        )

    def conversation(self, metadata: dict[str, object]) -> UUID:
        conversation_id = uuid4()
        self._insert(
            "agent_conversations",
            id=conversation_id,
            user_id=self.owner_id,
            pod_id=self.pod_id,
            conversation_type="CHAT",
            conversation_metadata=json.dumps(metadata),
        )
        return conversation_id

    def contact(self, *, phone: str) -> UUID:
        contact_id = uuid4()
        self._insert("contacts", id=contact_id, pod_id=self.pod_id)
        self._insert(
            "contact_identities",
            id=uuid4(),
            contact_id=contact_id,
            pod_id=self.pod_id,
            kind="PHONE",
            value=phone,
            strength="CHANNEL",
            verified_at="2026-10-01T09:00:00+00:00",
        )
        return contact_id

    def telegram_surface(self) -> UUID:
        surface_id = uuid4()
        self._insert(
            "agent_surfaces",
            id=surface_id,
            pod_id=self.pod_id,
            organization_id=self.organization_id,
            agent_id=self.agent_id,
            surface_type="TELEGRAM",
            name="telegram",
            config=json.dumps({}),
        )
        return surface_id

    def strangers_link(self, surface_id: UUID, conversation_id: UUID) -> None:
        self._insert(
            "agent_surface_conversation_links",
            id=uuid4(),
            surface_id=surface_id,
            conversation_id=conversation_id,
            platform="TELEGRAM",
            external_channel_id="-100123",
            external_thread_id="-100123",
            external_user_id="~outsiders",
            last_event=json.dumps({}),
        )

    def anonymous_visitor(self, conversation_id: UUID) -> UUID:
        widget_id, session_id = uuid4(), uuid4()
        self._insert(
            "agent_surface_web_widgets",
            id=widget_id,
            pod_id=self.pod_id,
            agent_id=self.agent_id,
            name="Site",
            public_key=f"pk_{widget_id.hex}",
            signing_secret="sealed",
            allowed_origins=json.dumps([]),
            answer="ANYONE",
        )
        self._insert(
            "visitor_sessions",
            id=session_id,
            pod_id=self.pod_id,
            widget_id=widget_id,
            strength="ANONYMOUS",
            conversation_id=conversation_id,
            secret_hash=session_id.hex,
            last_seen_at="2026-10-10T09:00:00+00:00",
            expires_at="2027-01-01T09:00:00+00:00",
        )
        return session_id


def test_every_outsider_comes_through_as_a_contact_and_every_member_untouched() -> None:
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

        migrate("upgrade", BEFORE)
        with psycopg.connect(plain_url, autocommit=True) as connection:
            pod = _Pod(connection)
            customer = pod.contact(phone="447700900123")
            customers_chat = pod.conversation(
                {"audience": "contact", "contact_id": str(customer), "x": 1}
            )
            strangers_thread = pod.conversation({"audience": "outsiders"})
            pod.strangers_link(pod.telegram_surface(), strangers_thread)
            visitors_chat = pod.conversation(
                {"audience": "outsiders", "source": "web_widget"}
            )
            visitor_session = pod.anonymous_visitor(visitors_chat)
            members_own = pod.conversation({"title_hint": "planning"})

        migrate("upgrade", AFTER)
        with psycopg.connect(plain_url) as connection:

            def conversation(conversation_id: UUID) -> tuple:
                row = connection.execute(
                    "SELECT contact_id, conversation_metadata, is_archived "
                    "FROM agent_conversations WHERE id = %s",
                    [str(conversation_id)],
                ).fetchone()
                assert row is not None
                return row

            # The contact named by metadata is named by the column, and the
            # metadata keys it replaced are gone; the others stay.
            contact_id, metadata, archived = conversation(customers_chat)
            assert contact_id == customer
            assert metadata == {"x": 1}
            assert not archived

            # The anonymous visitor is an anonymous contact, on session and
            # conversation alike, known by their browser's session.
            contact_id, metadata, _ = conversation(visitors_chat)
            assert contact_id is not None
            assert metadata == {"source": "web_widget"}
            session_contact = connection.execute(
                "SELECT contact_id FROM visitor_sessions WHERE id = %s",
                [str(visitor_session)],
            ).fetchone()
            assert session_contact == (contact_id,)
            handle = connection.execute(
                "SELECT kind, value, strength FROM contact_identities "
                "WHERE contact_id = %s",
                [str(contact_id)],
            ).fetchall()
            assert handle == [("WEB", str(visitor_session), "SESSION")]

            # Both had been answered, so the contact limit counts them.
            answered = connection.execute(
                "SELECT count(*) FROM contacts WHERE answered_at IS NOT NULL"
            ).fetchone()
            assert answered == (2,)

            # The strangers' shared thread is kept for reading, routed to by
            # nothing, and marked so nothing runs in it again.
            contact_id, metadata, archived = conversation(strangers_thread)
            assert contact_id is None
            assert metadata == {"retired_outsiders_thread": True}
            assert archived
            links = connection.execute(
                "SELECT count(*) FROM agent_surface_conversation_links "
                "WHERE external_user_id = '~outsiders'"
            ).fetchone()
            assert links == (0,)

            # A member's own conversation is exactly as it was.
            contact_id, metadata, archived = conversation(members_own)
            assert (contact_id, metadata, archived) == (
                None,
                {"title_hint": "planning"},
                False,
            )

        migrate("downgrade", BEFORE)
        with psycopg.connect(plain_url) as connection:
            restored = connection.execute(
                "SELECT conversation_metadata FROM agent_conversations WHERE id = %s",
                [str(customers_chat)],
            ).fetchone()
            assert restored == (
                {"x": 1, "audience": "contact", "contact_id": str(customer)},
            )

        migrate("upgrade", "head")
