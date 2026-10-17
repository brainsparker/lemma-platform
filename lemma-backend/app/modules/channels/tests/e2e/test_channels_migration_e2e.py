"""Migration 0058 places every thread and group the pod has in a channel.

Seeded at ``0057`` with one of each place a pod talks in -- a member's private
chat, the same member in a group, a contact's private chat, a web visitor --
and a group's log with a member, a stranger and the bot's answer in it. After
the upgrade each is a source of the right channel, and the log is the group
channel's timeline, every line said by the person it was.
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
BEFORE = "0057_contacts_for_everyone"
AFTER = "0058_channels"
GROUP_CHAT = "-100123"


class _Pod:
    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection
        self.organization_id = uuid4()
        self.pod_id = uuid4()
        self.owner_id = self._user("owner")
        self._insert(
            "organizations",
            id=self.organization_id,
            name="Acme",
            slug=f"acme-{self.organization_id.hex[:8]}",
            join_policy="INVITE",
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
        self.agent_id = uuid4()
        self._insert(
            "agents",
            id=self.agent_id,
            pod_id=self.pod_id,
            user_id=self.owner_id,
            name="kit",
            visibility="POD",
            instruction="",
            toolsets=json.dumps([]),
        )
        self.surface_id = uuid4()
        self._insert(
            "agent_surfaces",
            id=self.surface_id,
            pod_id=self.pod_id,
            organization_id=self.organization_id,
            agent_id=self.agent_id,
            surface_type="TELEGRAM",
            name="telegram",
            config=json.dumps({}),
        )

    def _insert(self, table: str, **values: object) -> None:
        columns = ", ".join(values)
        marks = ", ".join(["%s"] * len(values))
        self.connection.execute(
            f"INSERT INTO {table} ({columns}, created_at, updated_at) "
            f"VALUES ({marks}, now(), now())",
            [str(v) if isinstance(v, UUID) else v for v in values.values()],
        )

    def _user(self, name: str) -> UUID:
        user_id = uuid4()
        self._insert(
            "users",
            id=user_id,
            email=f"{name}-{user_id.hex[:6]}@acme.example",
            is_verified=True,
            is_active=True,
            is_superuser=False,
            is_deleted=False,
        )
        return user_id

    def member(self, name: str, *, telegram_id: str) -> UUID:
        user_id = self._user(name)
        seat = uuid4()
        self._insert(
            "organization_members",
            id=seat,
            user_id=user_id,
            organization_id=self.organization_id,
            role="MEMBER",
        )
        self._insert(
            "pod_members", id=uuid4(), pod_id=self.pod_id, organization_member_id=seat
        )
        self._insert(
            "agent_surface_external_users",
            id=uuid4(),
            platform="TELEGRAM",
            external_user_id=telegram_id,
            raw_profile=json.dumps({}),
            resolved_user_id=user_id,
        )
        return user_id

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

    def conversation(self, *, user_id: UUID, contact_id: UUID | None = None) -> UUID:
        conversation_id = uuid4()
        self._insert(
            "agent_conversations",
            id=conversation_id,
            user_id=user_id,
            pod_id=self.pod_id,
            contact_id=contact_id,
            conversation_type="CHAT",
            conversation_metadata=json.dumps({}),
        )
        return conversation_id

    def link(
        self,
        conversation_id: UUID,
        *,
        kind: str,
        user: str,
        channel: str,
        thread: str,
    ) -> UUID:
        link_id = uuid4()
        self._insert(
            "agent_surface_conversation_links",
            id=link_id,
            surface_id=self.surface_id,
            conversation_id=conversation_id,
            platform="TELEGRAM",
            external_channel_id=channel,
            external_thread_id=thread,
            external_user_id=user,
            conversation_kind=kind,
            last_event=json.dumps({}),
        )
        return link_id

    def group(self) -> UUID:
        group_id = uuid4()
        self._insert(
            "agent_surface_groups",
            id=group_id,
            pod_id=self.pod_id,
            surface_id=self.surface_id,
            platform="TELEGRAM",
            external_channel_id=GROUP_CHAT,
            title="Launch crew",
            owner_user_id=self.owner_id,
        )
        return group_id

    def line(self, group_id: UUID, *, body: str, **values: object) -> UUID:
        line_id = uuid4()
        self._insert(
            "agent_surface_group_messages",
            id=line_id,
            group_id=group_id,
            body=body,
            **values,
        )
        return line_id

    def visitor(self, *, contact_id: UUID, conversation_id: UUID) -> UUID:
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
            contact_id=contact_id,
            strength="ANONYMOUS",
            conversation_id=conversation_id,
            secret_hash=session_id.hex,
            last_seen_at="2026-10-10T09:00:00+00:00",
            expires_at="2027-01-01T09:00:00+00:00",
        )
        return session_id


def test_every_place_lands_in_its_channel_and_the_group_log_is_its_timeline() -> None:
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
            priya = pod.member("priya", telegram_id="priya-tg")
            priyas_chat = pod.conversation(user_id=priya)
            pod.link(priyas_chat, kind="DM", user="priya-tg", channel="9", thread="9")
            priya_in_the_group = pod.conversation(user_id=priya)
            pod.link(
                priya_in_the_group,
                kind="CHANNEL",
                user="priya-tg",
                channel=GROUP_CHAT,
                thread=GROUP_CHAT,
            )
            dana = pod.contact(phone="447700900123")
            danas_chat = pod.conversation(user_id=pod.owner_id, contact_id=dana)
            pod.link(
                danas_chat,
                kind="DM",
                user=f"~contact:{dana}",
                channel="7",
                thread="7",
            )
            visitor = pod.contact(phone="447700900999")
            visitors_chat = pod.conversation(user_id=pod.owner_id, contact_id=visitor)
            pod.visitor(contact_id=visitor, conversation_id=visitors_chat)
            group = pod.group()
            pod.line(
                group,
                body="Proofs are due Friday.",
                author_external_id="priya-tg",
                author_name="Priya",
                external_message_id="80",
            )
            pod.line(
                group,
                body="Can I see the brochure?",
                author_external_id="tom-tg",
                author_name="Tom",
                external_message_id="81",
            )
            pod.line(
                group,
                body="Friday, yes.",
                from_agent=True,
                answered_name="Priya",
                answered_user_id=priya,
            )

        migrate("upgrade", AFTER)
        with psycopg.connect(plain_url) as connection:

            def one(sql: str, *params: object) -> tuple:
                row = connection.execute(sql, [str(p) for p in params]).fetchone()
                assert row is not None, sql
                return row

            def channel_of(conversation_id: UUID) -> UUID:
                (channel_id,) = one(
                    "SELECT channel_id FROM agent_conversations WHERE id = %s",
                    conversation_id,
                )
                assert channel_id is not None
                return channel_id

            # Priya's private chat is a source of her direct channel.
            direct = channel_of(priyas_chat)
            assert one(
                "SELECT kind, member_user_id FROM channels WHERE id = %s", direct
            ) == (
                "DIRECT",
                priya,
            )
            assert one(
                "SELECT kind, external_channel_id, external_user_id "
                "FROM channel_sources WHERE channel_id = %s",
                direct,
            ) == ("PRIVATE", None, "priya-tg")

            # The group is a channel of its own, holding her conversation there.
            group_channel = channel_of(priya_in_the_group)
            assert one(
                "SELECT kind, title FROM channels WHERE id = %s", group_channel
            ) == ("GROUP", "Launch crew")
            (group_source,) = one(
                "SELECT id FROM channel_sources WHERE channel_id = %s "
                "AND kind = 'SHARED' AND external_channel_id = %s",
                group_channel,
                GROUP_CHAT,
            )

            # A contact's chat, and a web visitor's, each in their own channel.
            assert one(
                "SELECT kind, contact_id FROM channels WHERE id = %s",
                channel_of(danas_chat),
            ) == ("CONTACT", dana)
            visitors_channel = channel_of(visitors_chat)
            assert one(
                "SELECT kind, contact_id FROM channels WHERE id = %s",
                visitors_channel,
            ) == ("CONTACT", visitor)
            assert one(
                "SELECT platform FROM channel_sources WHERE channel_id = %s",
                visitors_channel,
            ) == ("WEB",)

            # Every link sits on a source.
            assert one(
                "SELECT count(*) FROM agent_surface_conversation_links "
                "WHERE source_id IS NULL"
            ) == (0,)

            # The log is the group's timeline, each line said by who said it:
            # Priya as a member, Tom as a contact made for him, the bot for Priya.
            lines = connection.execute(
                "SELECT body, sender_kind, sender_user_id, sender_contact_id, "
                "on_behalf_user_id, source_id FROM channel_messages "
                "WHERE channel_id = %s ORDER BY body",
                [str(group_channel)],
            ).fetchall()
            by_body = {line[0]: line[1:] for line in lines}
            assert by_body["Proofs are due Friday."][:2] == ("MEMBER", priya)
            tom = by_body["Can I see the brochure?"]
            assert tom[0] == "CONTACT" and tom[2] is not None
            assert by_body["Friday, yes."][0] == "AGENT"
            assert by_body["Friday, yes."][3] == priya
            assert {line[5] for line in lines} == {group_source}
            assert one(
                "SELECT kind, value FROM contact_identities WHERE contact_id = %s",
                tom[2],
            ) == ("TELEGRAM", "tom-tg")
            seats = {
                row[0]
                for row in connection.execute(
                    "SELECT coalesce(user_id, contact_id) FROM channel_participants "
                    "WHERE channel_id = %s",
                    [str(group_channel)],
                )
            }
            assert {priya, tom[2]} <= seats
            # The old log is kept for one release, for pods on the old image.
            assert one("SELECT count(*) FROM agent_surface_group_messages") == (3,)

        migrate("downgrade", BEFORE)
        with psycopg.connect(plain_url) as connection:
            restored = connection.execute(
                "SELECT count(*) FROM agent_surface_group_messages"
            ).fetchone()
            assert restored == (3,)

        migrate("upgrade", "head")
