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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base, UUIDAuditBase


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


class ChannelAgentModel(Base):
    """An agent that answers in a channel."""

    __tablename__ = "channel_agents"

    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )


class ChannelSourceModel(UUIDAuditBase):
    __tablename__ = "channel_sources"
    __table_args__ = (
        Index(
            "uq_channel_sources_place",
            "surface_id",
            "platform",
            text("coalesce(external_channel_id, '')"),
            text("coalesce(external_user_id, '')"),
            unique=True,
            postgresql_where=text("surface_id IS NOT NULL"),
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
