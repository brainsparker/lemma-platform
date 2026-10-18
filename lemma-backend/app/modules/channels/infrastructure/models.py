"""Tables for channels: the place, who is in it, where it is fed from, what was said.

Everything cascades from the pod. A channel's sources go with it; a source's
surface going away leaves the source -- and the history said there -- behind,
pointing at nothing. Nothing here is swept on a timer: a timeline is the pod's
until a member deletes it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class ChannelModel(UUIDAuditBase):
    __tablename__ = "channels"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'DIRECT') = (member_user_id IS NOT NULL)"
            " AND (kind = 'CONTACT') = (contact_id IS NOT NULL)",
            name="ck_channels_kind_names_its_person",
        ),
        Index(
            "uq_channels_direct_member",
            "pod_id",
            "member_user_id",
            unique=True,
            postgresql_where=text("kind = 'DIRECT'"),
        ),
        Index(
            "uq_channels_contact",
            "pod_id",
            "contact_id",
            unique=True,
            postgresql_where=text("kind = 'CONTACT'"),
        ),
        Index("ix_channels_pod_activity", "pod_id", "last_message_at", "id"),
        Index(
            "ix_channels_pod_attention",
            "pod_id",
            "attention_at",
            postgresql_where=text("attention_at IS NOT NULL"),
        ),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    member_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    handler_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    topic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Since when the channel has asked for a person -- an agent that could not
    #: help a contact, say -- and why. Cleared once somebody steps in.
    attention_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attention_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The channel's own settings (``domain.entities.ChannelSettings``); a key
    #: that is absent is its default.
    settings: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class ChannelParticipantModel(UUIDAuditBase):
    __tablename__ = "channel_participants"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(user_id, contact_id) = 1",
            name="ck_channel_participants_one_person",
        ),
        Index(
            "uq_channel_participants_member",
            "channel_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_channel_participants_contact",
            "channel_id",
            "contact_id",
            unique=True,
            postgresql_where=text("contact_id IS NOT NULL"),
        ),
        Index("ix_channel_participants_member", "user_id", "pod_id"),
    )

    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    last_read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Which lines reach them outside the app: ``ALL``, ``MENTIONS`` or ``NONE``.
    notify: Mapped[str] = mapped_column(
        String(10), nullable=False, default="ALL", server_default=text("'ALL'")
    )
    #: Where those lines go -- the platforms they chose -- or ``None`` for the
    #: pod's rule.
    notify_via: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)


class ChannelSourceModel(UUIDAuditBase):
    __tablename__ = "channel_sources"
    __table_args__ = (
        # One row per place a surface hears, per pod: a personal DM arrives
        # through a company's installation and is the person's own pod's. A
        # group the bot asked a platform to create has no id yet, and is keyed
        # by its request instead.
        Index(
            "uq_channel_sources_place",
            "pod_id",
            "surface_id",
            "platform",
            text("coalesce(external_channel_id, '')"),
            text("coalesce(external_user_id, '')"),
            unique=True,
            postgresql_where=text(
                "surface_id IS NOT NULL AND (external_channel_id IS NOT NULL"
                " OR external_user_id IS NOT NULL)"
            ),
        ),
        Index(
            "uq_channel_sources_request",
            "platform",
            "request_id",
            unique=True,
            postgresql_where=text("request_id IS NOT NULL"),
        ),
        # A web chat is a widget's, not a surface's: keyed by the widget and
        # the browser's session instead.
        Index(
            "uq_channel_sources_web_place",
            "pod_id",
            "platform",
            "external_channel_id",
            "external_user_id",
            unique=True,
            postgresql_where=text("surface_id IS NULL"),
        ),
        Index("ix_channel_sources_channel", "channel_id"),
        # The surfaces that hear one chat, and the private places of one person.
        Index("ix_channel_sources_chat", "platform", "external_channel_id"),
        Index(
            "ix_channel_sources_person",
            "platform",
            "external_user_id",
            postgresql_where=text("kind = 'PRIVATE'"),
        ),
    )

    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    surface_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="SET NULL"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    external_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: What the surface last heard here, as its adapter reads it: how to write
    #: into this place when nothing is being answered (a notification, a
    #: follow-up). The surface's own shape; nothing else reads inside it.
    delivery_ref: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    # -- a shared place the pod's bot is in: a group, a shared channel --------
    #: The member who answers for the people here from outside the pod.
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Whether people outside the pod are answered here at all.
    answers_outsiders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: A Slack channel shared with another company.
    shared_externally: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: The link people join by, where joining is by link (WhatsApp).
    invite_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: The platform's id for a creation the bot asked for; kept once confirmed,
    #: to recognise a replayed confirmation.
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ChannelMessageModel(UUIDAuditBase):
    __tablename__ = "channel_messages"
    __table_args__ = (
        # At most one: a member or an agent removed since leaves the line,
        # said by somebody no longer named (the ids are SET NULL).
        CheckConstraint(
            "num_nonnulls(sender_user_id, sender_contact_id, sender_agent_id) <= 1",
            name="ck_channel_messages_one_sender",
        ),
        CheckConstraint(
            "num_nonnulls(on_behalf_user_id, on_behalf_contact_id) <= 1"
            " AND (sender_kind = 'AGENT'"
            " OR num_nonnulls(on_behalf_user_id, on_behalf_contact_id) = 0)",
            name="ck_channel_messages_only_an_agent_acts_for_someone",
        ),
        Index("ix_channel_messages_timeline", "channel_id", "created_at", "id"),
        Index(
            "uq_channel_messages_external",
            "source_id",
            "external_message_id",
            unique=True,
            postgresql_where=text("external_message_id IS NOT NULL"),
        ),
        # A conversation's latest line, for the source it is answering.
        Index("ix_channel_messages_conversation", "conversation_id", "created_at"),
        Index(
            "ix_channel_messages_search",
            text("to_tsvector('simple', body)"),
            postgresql_using="gin",
        ),
        CheckConstraint(
            "(interaction_kind IS NULL) = (interaction_state IS NULL)"
            " AND num_nonnulls(awaits_user_id, awaits_contact_id) <= 1",
            name="ck_channel_messages_interaction_shape",
        ),
        Index(
            "ix_channel_messages_thread",
            "thread_root_id",
            "created_at",
            "id",
            postgresql_where=text("thread_root_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_messages_awaiting_member",
            "awaits_user_id",
            "pod_id",
            "created_at",
            postgresql_where=text("interaction_state = 'OPEN'"),
        ),
        Index(
            "ix_channel_messages_awaiting_contact",
            "awaits_contact_id",
            "created_at",
            postgresql_where=text("interaction_state = 'OPEN'"),
        ),
    )

    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
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
    members_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: On a line a person said on a platform: what its surface needs to answer
    #: it in place -- its thread, the message replied to, the number it came
    #: to. The surface's own shape; nothing else reads inside it.
    reply_ref: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    #: Files, resources and voice beside the text, in the envelope's shape.
    parts: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    reply_to_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("channel_messages.id", ondelete="SET NULL"), nullable=True
    )
    #: The line a thread hangs from, on every reply in it.
    thread_root_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("channel_messages.id", ondelete="SET NULL"), nullable=True
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # -- a question this line puts to one person, and their answer ------------
    #: ``ASK``, ``APPROVAL`` or ``FORM``; ``None`` on an ordinary line.
    interaction_kind: Mapped[str | None] = mapped_column(String(12), nullable=True)
    #: The question as asked -- choices, a form's fields -- and what it resumes:
    #: the paused tool call, or the workflow's wait.
    interaction: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    #: ``OPEN``, ``ANSWERED``, ``CANCELLED`` or ``EXPIRED``.
    interaction_state: Mapped[str | None] = mapped_column(String(12), nullable=True)
    #: The one person who may answer it: whom it is waiting on.
    awaits_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    awaits_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    answer: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    answered_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    answered_by_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )


class ChannelMentionModel(UUIDAuditBase):
    """Somebody a line names: a member, a contact or an agent, read or not."""

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
            "ix_channel_mentions_unread_member",
            "user_id",
            "pod_id",
            "created_at",
            postgresql_where=text("read_at IS NULL AND user_id IS NOT NULL"),
        ),
        Index("ix_channel_mentions_channel", "channel_id"),
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
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ChannelMessageDeliveryModel(UUIDAuditBase):
    """One place a line was sent out to -- a source, or a member's own
    platform for a notification -- and what became of it."""

    __tablename__ = "channel_message_deliveries"
    __table_args__ = (
        Index("ix_channel_message_deliveries_message", "message_id"),
        Index(
            "ix_channel_message_deliveries_external",
            "platform",
            "external_message_id",
            postgresql_where=text("external_message_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_message_deliveries_pending",
            "pod_id",
            "created_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("channel_messages.id", ondelete="CASCADE"), nullable=False
    )
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("channel_sources.id", ondelete="SET NULL"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    recipient_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    #: ``PENDING``, ``SENT``, ``FAILED`` or ``SKIPPED``.
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
