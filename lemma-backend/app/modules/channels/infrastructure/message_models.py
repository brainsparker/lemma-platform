"""Tables for what is said in a channel: its lines, who they name, the
questions they ask, and each send of a line outside Lemma.

A line is numbered per channel (``seq``). A question a line asks is its own
row, and every send to a place outside Lemma -- or push to a member -- is a
delivery row, unique per line and target.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base, UUIDAuditBase


class ChannelMessageModel(UUIDAuditBase):
    __tablename__ = "channel_messages"
    __table_args__ = (
        CheckConstraint(
            "sender_kind IN ('MEMBER', 'CONTACT', 'AGENT')",
            name="ck_channel_messages_sender_kind",
        ),
        # At most one: a member or an agent removed since leaves the line,
        # said by somebody no longer named (those ids are SET NULL).
        CheckConstraint(
            "num_nonnulls(sender_user_id, sender_contact_id, sender_agent_id) <= 1"
            " AND (sender_user_id IS NULL OR sender_kind = 'MEMBER')"
            " AND (sender_contact_id IS NULL OR sender_kind = 'CONTACT')"
            " AND (sender_agent_id IS NULL OR sender_kind = 'AGENT')",
            name="ck_channel_messages_one_sender",
        ),
        CheckConstraint(
            "num_nonnulls(on_behalf_user_id, on_behalf_contact_id) <= 1"
            " AND (sender_kind = 'AGENT'"
            " OR num_nonnulls(on_behalf_user_id, on_behalf_contact_id) = 0)",
            name="ck_channel_messages_only_an_agent_acts_for_someone",
        ),
        ForeignKeyConstraint(
            ["channel_id", "pod_id"],
            ["channels.id", "channels.pod_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("channel_id", "seq", name="uq_channel_messages_seq"),
        Index(
            "uq_channel_messages_client",
            "channel_id",
            "client_message_id",
            unique=True,
            postgresql_where=text("client_message_id IS NOT NULL"),
        ),
        Index(
            "uq_channel_messages_external",
            "source_id",
            text("md5(external_message_id)"),
            unique=True,
            postgresql_where=text("external_message_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_thread",
            "thread_root_id",
            "seq",
            postgresql_where=text("thread_root_id IS NOT NULL"),
        ),
        Index("ix_channel_messages_conversation", "conversation_id", "seq"),
        Index("ix_channel_messages_search", "search", postgresql_using="gin"),
        Index(
            "ix_channel_messages_sender_user",
            "sender_user_id",
            postgresql_where=text("sender_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_sender_contact",
            "sender_contact_id",
            postgresql_where=text("sender_contact_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_sender_agent",
            "sender_agent_id",
            postgresql_where=text("sender_agent_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_on_behalf_user",
            "on_behalf_user_id",
            postgresql_where=text("on_behalf_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_on_behalf_contact",
            "on_behalf_contact_id",
            postgresql_where=text("on_behalf_contact_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_run",
            "agent_run_id",
            postgresql_where=text("agent_run_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_reply_to",
            "reply_to_id",
            postgresql_where=text("reply_to_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_source",
            "source_id",
            postgresql_where=text("source_id IS NOT NULL"),
        ),
    )

    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    #: The line's place in its channel's timeline, gapless from 1.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: What a client named the line it wrote, so a retry is the same line.
    client_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("channel_sources.id", ondelete="SET NULL"), nullable=True
    )
    sender_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    sender_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sender_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    sender_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    on_behalf_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    on_behalf_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Mentions, files and links beside the text (``domain.parts``).
    parts: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', coalesce(body, ''))", persisted=True),
        nullable=True,
    )
    members_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    external_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: What answers a platform line in place: the adapter's own shape.
    reply_ref: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    reply_to_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("channel_messages.id", ondelete="SET NULL"), nullable=True
    )
    #: The line a thread hangs from, on every reply in it.
    thread_root_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("channel_messages.id", ondelete="CASCADE"), nullable=True
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ChannelMentionModel(UUIDAuditBase):
    """Somebody a line names: a member, a contact or an agent."""

    __tablename__ = "channel_mentions"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(user_id, contact_id, agent_id) = 1",
            name="ck_channel_mentions_one_named",
        ),
        Index(
            "uq_channel_mentions_named",
            "message_id",
            text("coalesce(user_id, contact_id, agent_id)"),
            unique=True,
        ),
        Index(
            "ix_channel_mentions_user",
            "user_id",
            "pod_id",
            "channel_id",
            "seq",
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_mentions_contact",
            "contact_id",
            postgresql_where=text("contact_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_mentions_agent",
            "agent_id",
            postgresql_where=text("agent_id IS NOT NULL"),
        ),
    )

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("channel_messages.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    #: The line's ``seq``: a mention is unread while it is past the member's
    #: ``last_read_seq``.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )


class ChannelQuestionModel(Base):
    """A question a line puts to one person, and what became of it."""

    __tablename__ = "channel_questions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('ASK', 'APPROVAL', 'FORM')", name="ck_channel_questions_kind"
        ),
        CheckConstraint(
            "state IN ('OPEN', 'ANSWERING', 'ANSWERED', 'CANCELLED', 'EXPIRED')",
            name="ck_channel_questions_state",
        ),
        CheckConstraint(
            "num_nonnulls(awaits_user_id, awaits_contact_id) = 1",
            name="ck_channel_questions_awaits_one",
        ),
        UniqueConstraint("pod_id", "ref", name="uq_channel_questions_ref"),
        Index(
            "ix_channel_questions_ref_prefix",
            "pod_id",
            text("ref text_pattern_ops"),
        ),
        Index(
            "ix_channel_questions_waiting_member",
            "awaits_user_id",
            "pod_id",
            postgresql_where=text("state = 'OPEN' AND awaits_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_questions_waiting_contact",
            "awaits_contact_id",
            postgresql_where=text("state = 'OPEN' AND awaits_contact_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_questions_expiry",
            "expires_at",
            postgresql_where=text("state = 'OPEN' AND expires_at IS NOT NULL"),
        ),
        Index(
            "ix_channel_questions_batch",
            "pod_id",
            "batch_id",
            postgresql_where=text("batch_id IS NOT NULL"),
        ),
        Index("ix_channel_questions_channel", "channel_id"),
    )

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("channel_messages.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    #: What asked it: ``{asker}:{…}`` -- ``tool:{conversation}:{call}``,
    #: ``wf:{run}:{node}``, ``msg:{conversation}:{key}``. Unique per pod.
    ref: Mapped[str] = mapped_column(Text, nullable=False)
    #: Questions sent together; the asker is woken when the last one closes.
    batch_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    state: Mapped[str] = mapped_column(
        String(10), nullable=False, default="OPEN", server_default=text("'OPEN'")
    )
    #: The question as asked: choices, a form's schema, an approval's tool.
    question: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    awaits_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    awaits_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    #: The answer that took effect -- the asker's, not the request's.
    answer: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    answered_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    answered_by_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set while an answer is being taken; a claim older than a minute is
    #: abandoned and may be taken again.
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ChannelMessageDeliveryModel(UUIDAuditBase):
    """One send of a line outside Lemma: to a place, or to a member's reach.

    The only record of whether it went: unique per line and target, so a
    retried job finds the row and never sends twice.
    """

    __tablename__ = "channel_message_deliveries"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(source_id, recipient_user_id) = 1",
            name="ck_channel_message_deliveries_one_target",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED', 'SKIPPED', 'DEAD')",
            name="ck_channel_message_deliveries_status",
        ),
        Index(
            "uq_channel_message_deliveries_target",
            "message_id",
            text("coalesce(source_id, recipient_user_id)"),
            unique=True,
        ),
        Index(
            "ix_channel_message_deliveries_due",
            "next_attempt_at",
            postgresql_where=text("status IN ('PENDING', 'FAILED')"),
        ),
        Index(
            "ix_channel_message_deliveries_external",
            "platform",
            text("md5(external_message_id)"),
            postgresql_where=text("external_message_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_message_deliveries_source",
            "source_id",
            postgresql_where=text("source_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_message_deliveries_recipient",
            "recipient_user_id",
            postgresql_where=text("recipient_user_id IS NOT NULL"),
        ),
    )

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("channel_messages.id", ondelete="CASCADE"), nullable=False
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    #: A place the line goes to (a contact's chat, a group) ...
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("channel_sources.id", ondelete="CASCADE"), nullable=True
    )
    #: ... or a member it is pushed to, wherever they are reached.
    recipient_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    #: The platform it went out on; set when sent.
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="PENDING", server_default=text("'PENDING'")
    )
    #: Why it was skipped or failed, in words a member reads.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
