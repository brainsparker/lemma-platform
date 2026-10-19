"""Tables for channels: the place, who is in it, where it is fed from, what was said.

Everything cascades from the pod. A place belongs to an external account -- a
Slack workspace, a Teams tenant, a WhatsApp number, an inbox, a web widget --
so reinstalling a bot keeps its places and their history. Lines are numbered
per channel (``seq``): the order a client reads, and what read state counts.
A question a line asks is its own row, and every send to a place outside Lemma
is a delivery row, unique per line and target.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base, UUIDAuditBase


class ExternalAccountModel(UUIDAuditBase):
    """An account a pod's bot speaks through: what a place belongs to."""

    __tablename__ = "external_accounts"
    __table_args__ = (
        UniqueConstraint("pod_id", "platform", "account_key"),
        UniqueConstraint("id", "pod_id", name="uq_external_accounts_id_pod"),
        Index("ix_external_accounts_surface", "surface_id"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The platform's own name for the account: a Slack team id, a Teams
    #: tenant id, a WhatsApp phone number id, a bot id, an inbox address, a
    #: web widget id.
    account_key: Mapped[str] = mapped_column(String(320), nullable=False)
    #: The installation it is reached through now; another after a reinstall.
    surface_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="SET NULL"), nullable=True
    )


class ChannelModel(UUIDAuditBase):
    __tablename__ = "channels"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('DIRECT', 'CONTACT', 'GROUP')", name="ck_channels_kind"
        ),
        CheckConstraint(
            "visibility IN ('public', 'private')", name="ck_channels_visibility"
        ),
        CheckConstraint(
            "status IN ('open', 'waiting_on_contact', 'closed')",
            name="ck_channels_status",
        ),
        CheckConstraint(
            "hand_back_after_hours BETWEEN 1 AND 720",
            name="ck_channels_hand_back_after_hours",
        ),
        CheckConstraint(
            "(kind = 'DIRECT') = (member_user_id IS NOT NULL)"
            " AND (kind = 'CONTACT') = (contact_id IS NOT NULL)",
            name="ck_channels_kind_names_its_person",
        ),
        CheckConstraint(
            "kind = 'GROUP' OR visibility = 'private'",
            name="ck_channels_only_a_group_is_public",
        ),
        UniqueConstraint("id", "pod_id", name="uq_channels_id_pod"),
        Index(
            "uq_channels_direct_member",
            "pod_id",
            "member_user_id",
            unique=True,
            postgresql_where=text("kind = 'DIRECT'"),
        ),
        Index(
            "uq_channels_contact",
            "contact_id",
            unique=True,
            postgresql_where=text("kind = 'CONTACT'"),
        ),
        Index("ix_channels_pod_activity", "pod_id", "last_line_at", "id"),
        Index(
            "ix_channels_assignee",
            "assignee_user_id",
            postgresql_where=text("assignee_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channels_created_by",
            "created_by_user_id",
            postgresql_where=text("created_by_user_id IS NOT NULL"),
        ),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: ``public``: every member of the pod may read it. ``private``: its
    #: members only. DIRECT and CONTACT are always private, read by their own
    #: rules.
    visibility: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(500), nullable=True)
    member_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # -- a contact's channel: who answers them ---------------------------------
    #: Whether its agents answer the contact at all.
    agents_answer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: How long an assignee who wrote holds the contact before the agent
    #: answers again.
    hand_back_after_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, default=12, server_default=text("12")
    )
    assignee_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When the assignee last wrote to the contact: the hand-back window runs
    #: from here.
    assignee_last_line_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default=text("'open'")
    )
    #: Since when the channel has asked for a person, and why.
    attention_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attention_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: When the contact was told a person will reply, this open period: the
    #: one acknowledgement it gets until a member writes or it is closed.
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # -- the timeline's head ---------------------------------------------------
    #: The newest line's number; the next line takes ``last_seq + 1`` under
    #: this row's lock.
    last_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    #: The newest line outside a thread, for lists; no FK (it is a pointer
    #: the timeline keeps, not a relation).
    last_line_id: Mapped[UUID | None] = mapped_column(nullable=True)
    last_line_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ChannelMemberModel(UUIDAuditBase):
    """Who is in a channel: a member, a contact, or an agent."""

    __tablename__ = "channel_members"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(user_id, contact_id, agent_id) = 1",
            name="ck_channel_members_one_principal",
        ),
        CheckConstraint(
            "role IN ('owner', 'member', 'guest')", name="ck_channel_members_role"
        ),
        CheckConstraint(
            "notify IN ('ALL', 'MENTIONS', 'NONE')", name="ck_channel_members_notify"
        ),
        CheckConstraint(
            "(agent_id IS NOT NULL) = (answers IS NOT NULL)"
            " AND (answers IS NULL OR answers IN ('MENTIONED', 'EVERY_LINE'))",
            name="ck_channel_members_agent_answers",
        ),
        ForeignKeyConstraint(
            ["channel_id", "pod_id"],
            ["channels.id", "channels.pod_id"],
            ondelete="CASCADE",
        ),
        Index(
            "uq_channel_members_user",
            "channel_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_channel_members_contact",
            "channel_id",
            "contact_id",
            unique=True,
            postgresql_where=text("contact_id IS NOT NULL"),
        ),
        Index(
            "uq_channel_members_agent",
            "channel_id",
            "agent_id",
            unique=True,
            postgresql_where=text("agent_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_members_user",
            "user_id",
            "pod_id",
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_members_contact",
            "contact_id",
            postgresql_where=text("contact_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_members_agent",
            "agent_id",
            postgresql_where=text("agent_id IS NOT NULL"),
        ),
    )

    channel_id: Mapped[UUID] = mapped_column(nullable=False)
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
    role: Mapped[str] = mapped_column(
        String(10), nullable=False, default="member", server_default=text("'member'")
    )
    #: Which lines reach a person outside the app.
    notify: Mapped[str] = mapped_column(
        String(10), nullable=False, default="ALL", server_default=text("'ALL'")
    )
    #: Where they reach them -- platforms they chose -- or ``None`` for where
    #: they last spoke to the pod.
    notify_via: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    muted_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: An agent's: ``MENTIONED`` answers when named, ``EVERY_LINE`` answers
    #: everything said here.
    answers: Mapped[str | None] = mapped_column(String(12), nullable=True)
    added_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ChannelReadStateModel(Base):
    """How far a member has read a channel: the newest line they have seen."""

    __tablename__ = "channel_read_state"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True
    )
    last_read_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ChannelSourceModel(UUIDAuditBase):
    __tablename__ = "channel_sources"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('PRIVATE', 'SHARED')", name="ck_channel_sources_kind"
        ),
        ForeignKeyConstraint(
            ["channel_id", "pod_id"],
            ["channels.id", "channels.pod_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["account_id", "pod_id"],
            ["external_accounts.id", "external_accounts.pod_id"],
            ondelete="CASCADE",
        ),
        # One row per place an account hears: a chat, or one person in it.
        Index(
            "uq_channel_sources_place",
            "account_id",
            text("coalesce(external_channel_id, '')"),
            text("coalesce(external_user_id, '')"),
            unique=True,
            postgresql_where=text(
                "external_channel_id IS NOT NULL OR external_user_id IS NOT NULL"
            ),
        ),
        # A group the bot asked a platform for has no id yet.
        Index(
            "uq_channel_sources_request",
            "account_id",
            "request_id",
            unique=True,
            postgresql_where=text("request_id IS NOT NULL"),
        ),
        Index("ix_channel_sources_channel", "channel_id", "last_inbound_at"),
        # What every inbound message reads: the chat it was said in, or the
        # person who said it privately.
        Index(
            "ix_channel_sources_chat",
            "external_channel_id",
            "platform",
            postgresql_where=text("external_user_id IS NULL"),
        ),
        Index(
            "ix_channel_sources_person",
            "external_user_id",
            "platform",
            postgresql_where=text("external_user_id IS NOT NULL"),
        ),
        Index(
            "ix_channel_sources_owner",
            "owner_user_id",
            postgresql_where=text("owner_user_id IS NOT NULL"),
        ),
    )

    channel_id: Mapped[UUID] = mapped_column(nullable=False)
    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    #: ``PRIVATE``: one person's chat with the bot. ``SHARED``: several people.
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: The platform keeps it to its members (a private Slack channel, a group
    #: DM, a Teams group chat, a WhatsApp or Telegram group): its channel is
    #: private in Lemma too.
    is_private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    external_channel_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: How the account writes into this place when nothing is being answered:
    #: the adapter's own shape.
    delivery_ref: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    # -- a shared place: its bot-side settings ---------------------------------
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    answers_outsiders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    shared_externally: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    invite_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


# Kept apart for size, and named here so every reader finds them in one place.
from app.modules.channels.infrastructure.message_models import (  # noqa: E402
    ChannelMentionModel,
    ChannelMessageDeliveryModel,
    ChannelMessageModel,
    ChannelQuestionModel,
)

__all__ = [
    "ChannelMemberModel",
    "ChannelMentionModel",
    "ChannelMessageDeliveryModel",
    "ChannelMessageModel",
    "ChannelModel",
    "ChannelQuestionModel",
    "ChannelReadStateModel",
    "ChannelSourceModel",
    "ExternalAccountModel",
]
