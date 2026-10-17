"""Channels: one shared place for each chat, with a timeline of who said what.

**`channels`** is the place: ``DIRECT`` (one member and the pod -- their private
chats on every platform), ``CONTACT`` (one contact and the pod) or ``GROUP`` (a
chat several people read). **`channel_sources`** are the outside places a
channel is fed by -- a private chat, a group, an email thread, a web chat --
each keyed by the surface it arrives on and its place there.
**`channel_participants`** are the members and contacts in it,
**`channel_agents`** the agents that answer there, and **`channel_messages`**
its timeline: who said each line, for whom an agent said it, where, and
whether it was a note for members only.

**`agent_conversations.channel_id`** names the channel a conversation's agent
works in; **`agent_surface_conversation_links.source_id`** the source a
person's thread is on. A link stays what it was -- one person's thread, mapped
to their conversation, and where to answer them.

**Backfill.** Every link is placed: a private chat or email in its member's
DIRECT channel or its contact's CONTACT channel, a group or a shared channel
in a GROUP channel of its own. Every group the pod knows gets its channel,
and its log becomes that channel's timeline -- each line said by the member
it resolves to, else by the contact its handle names, made if there is none.
A web visitor's conversation is placed in their CONTACT channel.

**`agent_surface_group_messages`** stays, for one release: nothing in this
version reads or writes it, but a pod still running the previous image writes
every group message there, and dropping it under that pod would fail the
message's ingest. A later migration drops it once no pod runs the old image.

Revision ID: 0058_channels
Revises: 0057_contacts_for_everyone
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0058_channels"
down_revision = "0057_contacts_for_everyone"
branch_labels = None
depends_on = None


def _audit() -> list[sa.Column]:
    """``UUIDAuditBase``'s columns, new for each table."""
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def _create_tables() -> None:
    op.create_table(
        "channels",
        *_audit(),
        sa.Column(
            "pod_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column(
            "member_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "handler_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(kind = 'DIRECT') = (member_user_id IS NOT NULL)"
            " AND (kind = 'CONTACT') = (contact_id IS NOT NULL)",
            name="ck_channels_kind_names_its_person",
        ),
    )
    op.create_index(
        "uq_channels_direct_member",
        "channels",
        ["pod_id", "member_user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'DIRECT'"),
    )
    op.create_index(
        "uq_channels_contact",
        "channels",
        ["pod_id", "contact_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'CONTACT'"),
    )
    op.create_index(
        "ix_channels_pod_activity", "channels", ["pod_id", "last_message_at", "id"]
    )

    op.create_table(
        "channel_participants",
        *_audit(),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(user_id, contact_id) = 1",
            name="ck_channel_participants_one_person",
        ),
    )
    op.create_index(
        "uq_channel_participants_member",
        "channel_participants",
        ["channel_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_channel_participants_contact",
        "channel_participants",
        ["channel_id", "contact_id"],
        unique=True,
        postgresql_where=sa.text("contact_id IS NOT NULL"),
    )
    op.create_index(
        "ix_channel_participants_member",
        "channel_participants",
        ["user_id", "pod_id"],
    )

    op.create_table(
        "channel_agents",
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "channel_sources",
        *_audit(),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "surface_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_surfaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("external_channel_id", sa.String(255), nullable=True),
        sa.Column("external_user_id", sa.String(255), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_channel_sources_place ON channel_sources "
        "(surface_id, platform, coalesce(external_channel_id, ''), "
        "coalesce(external_user_id, '')) WHERE surface_id IS NOT NULL"
    )
    op.create_index(
        "uq_channel_sources_web_place",
        "channel_sources",
        ["pod_id", "platform", "external_channel_id", "external_user_id"],
        unique=True,
        postgresql_where=sa.text("surface_id IS NULL"),
    )
    op.create_index("ix_channel_sources_channel", "channel_sources", ["channel_id"])

    op.create_table(
        "channel_messages",
        *_audit(),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channel_sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sender_kind", sa.String(10), nullable=False),
        sa.Column(
            "sender_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "sender_contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "sender_agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "on_behalf_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "on_behalf_contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("sender_name", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("members_only", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("external_message_id", sa.String(255), nullable=True),
        sa.Column("external_thread_id", sa.String(255), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(sender_user_id, sender_contact_id, sender_agent_id) <= 1",
            name="ck_channel_messages_one_sender",
        ),
        sa.CheckConstraint(
            "num_nonnulls(on_behalf_user_id, on_behalf_contact_id) <= 1"
            " AND (sender_kind = 'AGENT'"
            " OR num_nonnulls(on_behalf_user_id, on_behalf_contact_id) = 0)",
            name="ck_channel_messages_only_an_agent_acts_for_someone",
        ),
    )
    op.create_index(
        "ix_channel_messages_timeline",
        "channel_messages",
        ["channel_id", "created_at", "id"],
    )
    op.create_index(
        "uq_channel_messages_external",
        "channel_messages",
        ["source_id", "external_message_id"],
        unique=True,
        postgresql_where=sa.text("external_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_channel_messages_conversation",
        "channel_messages",
        ["conversation_id", "created_at"],
    )
    op.execute(
        "CREATE INDEX ix_channel_messages_search ON channel_messages "
        "USING gin (to_tsvector('simple', body))"
    )


def backfill_statements() -> list[str]:
    """The data half, in order."""
    return [
        # Where each link is: its pod, the person it is, the place it is.
        """
        CREATE TEMPORARY TABLE link_places ON COMMIT DROP AS
        SELECT link.id AS link_id,
               link.conversation_id,
               -- The conversation's pod, not the surface's: a personal DM
               -- arrives through a company's installation and is answered in
               -- the person's own pod.
               conversation.pod_id,
               link.surface_id,
               link.platform,
               CASE WHEN link.conversation_kind = 'CHANNEL'
                    THEN 'SHARED' ELSE 'PRIVATE' END AS kind,
               CASE link.conversation_kind
                    WHEN 'CHANNEL' THEN link.external_channel_id
                    WHEN 'EMAIL' THEN link.external_thread_id
                    ELSE NULL END AS place_channel,
               CASE WHEN link.conversation_kind = 'CHANNEL'
                    THEN NULL ELSE link.external_user_id END AS place_user,
               conversation.user_id,
               conversation.contact_id,
               coalesce(
                   grp.title,
                   conversation.conversation_metadata->>'channel_name'
               ) AS title
        FROM agent_surface_conversation_links AS link
        JOIN agent_conversations AS conversation
          ON conversation.id = link.conversation_id
        LEFT JOIN agent_surface_groups AS grp
          ON grp.surface_id = link.surface_id
         AND grp.external_channel_id = link.external_channel_id
        """,
        # Groups the pod knows with no thread yet still get their channel.
        """
        INSERT INTO link_places
            (link_id, conversation_id, pod_id, surface_id, platform, kind,
             place_channel, place_user, user_id, contact_id, title)
        SELECT NULL, NULL, grp.pod_id, grp.surface_id, grp.platform, 'SHARED',
               grp.external_channel_id, NULL, NULL, NULL, grp.title
        FROM agent_surface_groups AS grp
        WHERE grp.external_channel_id IS NOT NULL
        """,
        # The channels: a member's, a contact's, and one per shared place.
        """
        INSERT INTO channels (id, pod_id, kind, member_user_id)
        SELECT gen_random_uuid(), pod_id, 'DIRECT', user_id
        FROM (SELECT DISTINCT pod_id, user_id FROM link_places
              WHERE kind = 'PRIVATE' AND contact_id IS NULL) AS members
        ON CONFLICT DO NOTHING
        """,
        """
        INSERT INTO channels (id, pod_id, kind, contact_id)
        SELECT gen_random_uuid(), pod_id, 'CONTACT', contact_id
        FROM (SELECT DISTINCT pod_id, contact_id FROM link_places
              WHERE kind = 'PRIVATE' AND contact_id IS NOT NULL) AS contacts
        ON CONFLICT DO NOTHING
        """,
        """
        CREATE TEMPORARY TABLE shared_places ON COMMIT DROP AS
        SELECT gen_random_uuid() AS channel_id, pod_id, surface_id, platform,
               place_channel, max(title) AS title
        FROM link_places WHERE kind = 'SHARED'
        GROUP BY pod_id, surface_id, platform, place_channel
        """,
        """
        INSERT INTO channels (id, pod_id, kind, title)
        SELECT channel_id, pod_id, 'GROUP', title FROM shared_places
        """,
        # The sources, each on the channel its place belongs to.
        """
        INSERT INTO channel_sources
            (id, channel_id, pod_id, surface_id, platform, kind,
             external_channel_id, external_user_id, title)
        SELECT gen_random_uuid(), channel_id, pod_id, surface_id, platform,
               'SHARED', place_channel, NULL, title
        FROM shared_places
        """,
        """
        INSERT INTO channel_sources
            (id, channel_id, pod_id, surface_id, platform, kind,
             external_channel_id, external_user_id)
        SELECT DISTINCT ON (place.surface_id, place.platform,
                            coalesce(place.place_channel, ''),
                            coalesce(place.place_user, ''))
               gen_random_uuid(), channel.id, place.pod_id, place.surface_id,
               place.platform, 'PRIVATE', place.place_channel, place.place_user
        FROM link_places AS place
        JOIN channels AS channel
          ON channel.pod_id = place.pod_id
         AND ((place.contact_id IS NULL AND channel.kind = 'DIRECT'
               AND channel.member_user_id = place.user_id)
           OR (place.contact_id IS NOT NULL AND channel.kind = 'CONTACT'
               AND channel.contact_id = place.contact_id))
        WHERE place.kind = 'PRIVATE'
        ON CONFLICT DO NOTHING
        """,
        # Each link on its source, each conversation in its channel.
        """
        UPDATE agent_surface_conversation_links AS link
        SET source_id = source.id
        FROM link_places AS place, channel_sources AS source
        WHERE place.link_id = link.id
          AND source.surface_id = place.surface_id
          AND source.platform = place.platform
          AND coalesce(source.external_channel_id, '')
              = coalesce(place.place_channel, '')
          AND coalesce(source.external_user_id, '')
              = coalesce(place.place_user, '')
        """,
        """
        UPDATE agent_conversations AS conversation
        SET channel_id = source.channel_id
        FROM agent_surface_conversation_links AS link
        JOIN channel_sources AS source ON source.id = link.source_id
        WHERE link.conversation_id = conversation.id
          AND conversation.channel_id IS NULL
        """,
        # A web visitor's conversation: their CONTACT channel, on a WEB source.
        """
        INSERT INTO channels (id, pod_id, kind, contact_id)
        SELECT DISTINCT ON (session.pod_id, session.contact_id)
               gen_random_uuid(), session.pod_id, 'CONTACT', session.contact_id
        FROM visitor_sessions AS session
        WHERE session.contact_id IS NOT NULL
          AND session.conversation_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """,
        """
        INSERT INTO channel_sources
            (id, channel_id, pod_id, surface_id, platform, kind,
             external_channel_id, external_user_id)
        SELECT gen_random_uuid(), channel.id, session.pod_id, NULL, 'WEB',
               'PRIVATE', session.widget_id::text, session.id::text
        FROM visitor_sessions AS session
        JOIN channels AS channel
          ON channel.pod_id = session.pod_id
         AND channel.kind = 'CONTACT'
         AND channel.contact_id = session.contact_id
        WHERE session.conversation_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """,
        """
        UPDATE agent_conversations AS conversation
        SET channel_id = channel.id
        FROM visitor_sessions AS session
        JOIN channels AS channel
          ON channel.pod_id = session.pod_id
         AND channel.kind = 'CONTACT'
         AND channel.contact_id = session.contact_id
        WHERE session.conversation_id = conversation.id
          AND conversation.channel_id IS NULL
        """,
        # Everybody who spoke in a logged group, by who they are in the pod.
        """
        CREATE TEMPORARY TABLE group_speakers ON COMMIT DROP AS
        SELECT DISTINCT grp.pod_id, grp.platform, line.author_external_id,
               max(line.author_name) AS author_name,
               (SELECT person.resolved_user_id
                FROM agent_surface_external_users AS person
                JOIN organization_members AS seat
                  ON seat.user_id = person.resolved_user_id
                JOIN pod_members AS member
                  ON member.organization_member_id = seat.id
                 AND member.pod_id = grp.pod_id
                WHERE person.platform = grp.platform
                  AND person.external_user_id = line.author_external_id
                LIMIT 1) AS user_id,
               NULL::uuid AS contact_id
        FROM agent_surface_group_messages AS line
        JOIN agent_surface_groups AS grp ON grp.id = line.group_id
        WHERE NOT line.from_agent AND line.author_external_id IS NOT NULL
        GROUP BY grp.pod_id, grp.platform, line.author_external_id
        """,
        """
        UPDATE group_speakers AS speaker
        SET contact_id = identity.contact_id
        FROM contact_identities AS identity
        WHERE speaker.user_id IS NULL
          AND identity.pod_id = speaker.pod_id
          AND identity.kind = CASE speaker.platform
                                  WHEN 'WHATSAPP' THEN 'PHONE'
                                  ELSE speaker.platform END
          AND identity.value = CASE speaker.platform
                                   WHEN 'WHATSAPP'
                                   THEN regexp_replace(
                                       speaker.author_external_id, '\\D', '', 'g')
                                   ELSE speaker.author_external_id END
        """,
        """
        UPDATE group_speakers SET contact_id = gen_random_uuid()
        WHERE user_id IS NULL AND contact_id IS NULL
          AND platform IN ('TELEGRAM', 'WHATSAPP')
        """,
        """
        INSERT INTO contacts (id, pod_id, display_name, created_at, updated_at)
        SELECT speaker.contact_id, speaker.pod_id, speaker.author_name, now(), now()
        FROM group_speakers AS speaker
        WHERE speaker.contact_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM contacts AS c
                          WHERE c.id = speaker.contact_id)
        """,
        """
        INSERT INTO contact_identities
            (id, contact_id, pod_id, kind, value, strength, verified_at,
             created_at, updated_at)
        SELECT gen_random_uuid(), speaker.contact_id, speaker.pod_id,
               CASE speaker.platform WHEN 'WHATSAPP' THEN 'PHONE'
                    ELSE speaker.platform END,
               CASE speaker.platform
                    WHEN 'WHATSAPP'
                    THEN regexp_replace(speaker.author_external_id, '\\D', '', 'g')
                    ELSE speaker.author_external_id END,
               'CHANNEL', now(), now(), now()
        FROM group_speakers AS speaker
        WHERE speaker.contact_id IS NOT NULL
        ON CONFLICT ON CONSTRAINT uq_contact_identities_pod_handle DO NOTHING
        """,
        # The log, as the group channel's timeline.
        """
        INSERT INTO channel_messages
            (id, created_at, updated_at, channel_id, pod_id, source_id,
             sender_kind, sender_user_id, sender_contact_id, sender_agent_id,
             on_behalf_user_id, sender_name, body, external_message_id)
        SELECT line.id, line.created_at, line.created_at, source.channel_id,
               grp.pod_id, source.id,
               CASE WHEN line.from_agent THEN 'AGENT'
                    WHEN speaker.user_id IS NOT NULL THEN 'MEMBER'
                    ELSE 'CONTACT' END,
               CASE WHEN NOT line.from_agent THEN speaker.user_id END,
               CASE WHEN NOT line.from_agent AND speaker.user_id IS NULL
                    THEN speaker.contact_id END,
               CASE WHEN line.from_agent THEN surface.agent_id END,
               CASE WHEN line.from_agent THEN line.answered_user_id END,
               CASE WHEN line.from_agent THEN line.answered_name
                    ELSE line.author_name END,
               line.body,
               line.external_message_id
        FROM agent_surface_group_messages AS line
        JOIN agent_surface_groups AS grp ON grp.id = line.group_id
        JOIN agent_surfaces AS surface ON surface.id = grp.surface_id
        JOIN channel_sources AS source
          ON source.surface_id = grp.surface_id
         AND source.platform = grp.platform
         AND source.external_channel_id = grp.external_channel_id
         AND source.external_user_id IS NULL
        LEFT JOIN group_speakers AS speaker
          ON speaker.pod_id = grp.pod_id
         AND speaker.platform = grp.platform
         AND speaker.author_external_id = line.author_external_id
        WHERE line.from_agent
           OR speaker.user_id IS NOT NULL
           OR speaker.contact_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """,
        # Seats for everybody who spoke, and the channels' last activity.
        """
        INSERT INTO channel_participants (id, channel_id, pod_id, user_id, contact_id)
        SELECT gen_random_uuid(), channel_id, pod_id, sender_user_id,
               sender_contact_id
        FROM (SELECT DISTINCT channel_id, pod_id, sender_user_id, sender_contact_id
              FROM channel_messages
              WHERE sender_user_id IS NOT NULL
                 OR sender_contact_id IS NOT NULL) AS speakers
        ON CONFLICT DO NOTHING
        """,
        """
        INSERT INTO channel_participants (id, channel_id, pod_id, user_id, contact_id)
        SELECT gen_random_uuid(), id, pod_id, member_user_id, contact_id
        FROM channels WHERE kind IN ('DIRECT', 'CONTACT')
        ON CONFLICT DO NOTHING
        """,
        """
        UPDATE channels AS channel
        SET last_message_at = latest.at
        FROM (SELECT channel_id, max(created_at) AS at
              FROM channel_messages GROUP BY channel_id) AS latest
        WHERE latest.channel_id = channel.id
        """,
    ]


def upgrade() -> None:
    _create_tables()
    op.add_column(
        "agent_conversations",
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channels.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_surface_conversation_links",
        sa.Column(
            "source_id",
            UUID(as_uuid=True),
            sa.ForeignKey("channel_sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    for statement in backfill_statements():
        op.execute(sa.text(statement))
    op.create_index(
        "ix_agent_surface_conversation_links_source_id",
        "agent_surface_conversation_links",
        ["source_id"],
    )
    # Concurrently, outside the transaction: conversations are written on
    # every turn, and a plain build would block those writers.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_agent_conversations_channel_id",
            "agent_conversations",
            ["channel_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_agent_conversations_channel_id",
            table_name="agent_conversations",
            postgresql_concurrently=True,
            if_exists=True,
        )
    # The log was kept; what the timeline heard since joins it. A line copied
    # in by the upgrade kept its id, and one an old pod logged as well keeps
    # its message id, so neither is written twice.
    op.execute(
        sa.text(
            """
            INSERT INTO agent_surface_group_messages
                (id, created_at, updated_at, group_id, external_message_id,
                 author_external_id, author_name, from_agent, body,
                 answered_name, answered_user_id)
            SELECT message.id, message.created_at, message.created_at, grp.id,
                   message.external_message_id, NULL, message.sender_name,
                   message.sender_kind = 'AGENT', message.body,
                   CASE WHEN message.sender_kind = 'AGENT'
                        THEN message.sender_name END,
                   message.on_behalf_user_id
            FROM channel_messages AS message
            JOIN channel_sources AS source ON source.id = message.source_id
            JOIN agent_surface_groups AS grp
              ON grp.surface_id = source.surface_id
             AND grp.external_channel_id = source.external_channel_id
            WHERE source.kind = 'SHARED'
            ON CONFLICT DO NOTHING
            """
        )
    )
    op.drop_index(
        "ix_agent_surface_conversation_links_source_id",
        table_name="agent_surface_conversation_links",
    )
    op.drop_column("agent_surface_conversation_links", "source_id")
    op.drop_column("agent_conversations", "channel_id")
    for table in (
        "channel_messages",
        "channel_sources",
        "channel_agents",
        "channel_participants",
        "channels",
    ):
        op.drop_table(table)
