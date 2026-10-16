"""Every outsider is a contact, and a conversation names the contact it answers.

Until now three kinds of person outside a pod were answered three ways: a
contact (a vouched handle, in a private chat), a group's stranger (nobody,
sharing one ``~outsiders`` conversation per group with every other stranger)
and an anonymous web visitor (nobody, on a session). From here every one of
them is a contact, and whom a conversation answers is a column rather than two
metadata keys a run had to trust.

**`agent_conversations.contact_id`** names the contact a conversation answers,
cascading from the contact: forgetting them forgets what was said with them.
Filled from the ``audience``/``contact_id`` metadata it replaces, which is then
removed.

**Anonymous web visitors become anonymous contacts.** Each session that has a
conversation and no contact gets one, known by a ``WEB`` handle (the session's
id, ``SESSION`` strength), and the session and its conversation are pointed at
it.

**The shared ``~outsiders`` conversations retire.** A stranger's next question
opens a conversation of their own, as their contact. The old thread is kept for
reading -- archived, its routing links gone, and marked
``retired_outsiders_thread`` so nothing runs in it again: it holds strangers'
words and belongs to a member, and a run there would read the one with the
other's access.

**`contacts.merged_into_id`** points a contact that turned out to be another at
the one it became; **`contacts.answered_at`** is when an agent first answered
them -- set here for every contact that already has a conversation.
**`usage_contacts_caps.max_contacts`** is how many contacts an organization's
bots may answer at all; null for no limit.

Revision ID: 0057_contacts_for_everyone
Revises: 0056_agent_surface_bundle_work
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0057_contacts_for_everyone"
down_revision = "0056_agent_surface_bundle_work"
branch_labels = None
depends_on = None

#: The metadata keys the column replaces.
_AUDIENCE = "audience"
_CONTACT = "contact_id"
#: What marks a retired strangers' thread; ``agent.domain.outsiders``.
_RETIRED = "retired_outsiders_thread"


def _repair_invalid(name: str) -> None:
    """Drop a half-built index so ``IF NOT EXISTS`` stops mistaking it for done."""
    half_built = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": name},
        )
        .scalar()
    )
    if half_built:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def backfill_statements() -> list[str]:
    """The data half, in order. Each is idempotent: a rerun changes nothing."""
    return [
        # A contact's conversation, named by its metadata, by its column now.
        f"""
        UPDATE agent_conversations AS conversation
        SET contact_id = contact.id
        FROM contacts AS contact
        WHERE conversation.contact_id IS NULL
          AND conversation.conversation_metadata->>'{_AUDIENCE}' = 'contact'
          AND contact.id::text = conversation.conversation_metadata->>'{_CONTACT}'
          AND contact.pod_id = conversation.pod_id
        """,
        # Anonymous web visitors with a conversation: one contact each.
        """
        CREATE TEMPORARY TABLE anonymous_visitors ON COMMIT DROP AS
        SELECT session.id AS session_id,
               session.pod_id,
               session.conversation_id,
               session.created_at,
               gen_random_uuid() AS contact_id
        FROM visitor_sessions AS session
        WHERE session.contact_id IS NULL AND session.conversation_id IS NOT NULL
        """,
        """
        INSERT INTO contacts (id, pod_id, display_name, created_at, updated_at)
        SELECT contact_id, pod_id, NULL, created_at, now() FROM anonymous_visitors
        """,
        """
        INSERT INTO contact_identities
            (id, contact_id, pod_id, kind, value, strength, verified_at,
             created_at, updated_at)
        SELECT gen_random_uuid(), contact_id, pod_id, 'WEB', session_id::text,
               'SESSION', created_at, now(), now()
        FROM anonymous_visitors
        ON CONFLICT ON CONSTRAINT uq_contact_identities_pod_handle DO NOTHING
        """,
        """
        UPDATE visitor_sessions AS session
        SET contact_id = visitor.contact_id
        FROM anonymous_visitors AS visitor
        WHERE session.id = visitor.session_id AND session.contact_id IS NULL
        """,
        """
        UPDATE agent_conversations AS conversation
        SET contact_id = visitor.contact_id
        FROM anonymous_visitors AS visitor
        WHERE conversation.id = visitor.conversation_id
          AND conversation.contact_id IS NULL
        """,
        # Everybody already talked to has been answered.
        """
        UPDATE contacts AS contact
        SET answered_at = contact.created_at
        WHERE contact.answered_at IS NULL
          AND EXISTS (
              SELECT 1 FROM agent_conversations AS conversation
              WHERE conversation.contact_id = contact.id
          )
        """,
        # The shared strangers' threads retire: read-only, routed to no more.
        f"""
        UPDATE agent_conversations
        SET is_archived = true,
            conversation_metadata = conversation_metadata
                || jsonb_build_object('{_RETIRED}', true)
        WHERE contact_id IS NULL
          AND conversation_metadata->>'{_AUDIENCE}' = 'outsiders'
        """,
        """
        DELETE FROM agent_surface_conversation_links
        WHERE external_user_id = '~outsiders'
        """,
        # The keys the column replaced.
        f"""
        UPDATE agent_conversations
        SET conversation_metadata = conversation_metadata - '{_AUDIENCE}' - '{_CONTACT}'
        WHERE conversation_metadata ? '{_AUDIENCE}'
           OR conversation_metadata ? '{_CONTACT}'
        """,
    ]


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column(
            "merged_into_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        if_not_exists=True,
    )
    op.add_column(
        "contacts",
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        if_not_exists=True,
    )
    op.add_column(
        "usage_contacts_caps",
        sa.Column("max_contacts", sa.Integer(), nullable=True),
        if_not_exists=True,
    )
    op.add_column(
        "agent_conversations",
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        if_not_exists=True,
    )
    for statement in backfill_statements():
        op.execute(sa.text(statement))
    op.create_index(
        "ix_contacts_merged_into",
        "contacts",
        ["merged_into_id"],
        if_not_exists=True,
    )
    # Concurrently, outside the transaction: conversations are written on
    # every turn, and a plain build would block those writers.
    with op.get_context().autocommit_block():
        _repair_invalid("ix_agent_conversations_contact_id")
        op.create_index(
            "ix_agent_conversations_contact_id",
            "agent_conversations",
            ["contact_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_agent_conversations_contact_id",
            table_name="agent_conversations",
            postgresql_concurrently=True,
            if_exists=True,
        )
    # Whom a conversation answers goes back to its metadata.
    op.execute(
        sa.text(
            f"""
            UPDATE agent_conversations
            SET conversation_metadata = coalesce(conversation_metadata, '{{}}'::jsonb)
                || jsonb_build_object(
                    '{_AUDIENCE}', 'contact', '{_CONTACT}', contact_id::text
                )
            WHERE contact_id IS NOT NULL
            """
        )
    )
    op.drop_column("agent_conversations", "contact_id")
    op.drop_column("usage_contacts_caps", "max_contacts")
    op.drop_index("ix_contacts_merged_into", table_name="contacts", if_exists=True)
    op.drop_column("contacts", "answered_at")
    op.drop_column("contacts", "merged_into_id")
