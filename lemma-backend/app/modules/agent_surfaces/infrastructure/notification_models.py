"""The notifications table: something the pod needs a person to see.

Its own module because ``models.py`` is at the size ceiling. ``models.py``
re-exports it, so importing that file still registers ``notifications`` on
``Base.metadata`` for ``migrations/env.py``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase


class NotificationModel(UUIDAuditBase):
    """Retired: what an agent once told a member is a line in their own channel
    now (``channels``). Kept defined until a later migration drops the table,
    so the schema and this metadata agree in the meantime. Nothing reads or
    writes it."""

    __tablename__ = "notifications"
    __table_args__ = (
        # The inbox query: this person's notifications in this pod, newest first.
        Index(
            "ix_notifications_recipient_inbox",
            "pod_id",
            "recipient_user_id",
            "status",
            "created_at",
        ),
        # The reply path: does the conversation this inbound landed in have
        # anything open addressed to its owner?
        Index(
            "ix_notifications_delivery_conversation",
            "delivery_conversation_id",
            "status",
        ),
        Index("ix_notifications_origin", "origin_kind", "origin_id"),
        # The expiry sweep only ever scans OPEN rows with a deadline.
        Index(
            "ix_notifications_open_expires_at",
            "expires_at",
            postgresql_where=text("status = 'OPEN'"),
        ),
        # Pod-scoped rather than global: the key encodes a run/node id, and two
        # pods can legitimately never collide, but a global unique index would
        # make one pod's retry key a landmine for another's.
        UniqueConstraint(
            "pod_id", "idempotency_key", name="uq_notifications_idempotency"
        ),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recipient_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recipient_pod_member_id: Mapped[UUID] = mapped_column(
        ForeignKey("pod_members.id", ondelete="CASCADE"), index=True, nullable=False
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    origin_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    origin_id: Mapped[UUID | None] = mapped_column(nullable=True)
    origin_conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )
    # From a stranger's thread: answered only as words the recipient approved.
    from_outside: Mapped[bool] = mapped_column(Boolean, server_default="false")
    origin_group_title: Mapped[str | None] = mapped_column(String(255))
    asked_in_private: Mapped[bool] = mapped_column(Boolean, server_default="false")
    asked_by_name: Mapped[str | None] = mapped_column(String(255))

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    background_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    expects_response: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    action: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    delivery_status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    delivery_surface_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_surfaces.id", ondelete="SET NULL"), nullable=True
    )
    delivery_conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True
    )
    delivery_platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    response_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
