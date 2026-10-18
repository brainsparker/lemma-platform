"""Rows to entities, for every reader of the channel tables.

Kept apart so the repositories that read channels, sources, people and lines
all map a row the same way.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Interaction,
    InteractionKind,
    InteractionState,
    NotifyLevel,
    Participant,
    Principal,
    Sender,
    SenderKind,
    Source,
    SourceKind,
    read_settings,
)
from app.modules.channels.infrastructure.models import (
    ChannelMessageModel,
    ChannelModel,
    ChannelParticipantModel,
    ChannelSourceModel,
)

#: The most rows one read of a timeline, a list or a page returns.
MAX_LINES = 200


def now() -> datetime:
    return datetime.now(timezone.utc)


def channel_entity(row: ChannelModel) -> Channel:
    return Channel(
        id=row.id,
        pod_id=row.pod_id,
        kind=ChannelKind(row.kind),
        title=row.title,
        member_user_id=row.member_user_id,
        contact_id=row.contact_id,
        handler_user_id=row.handler_user_id,
        created_at=row.created_at,
        last_message_at=row.last_message_at,
        topic=row.topic,
        created_by_user_id=row.created_by_user_id,
        archived_at=row.archived_at,
        attention_at=row.attention_at,
        attention_reason=row.attention_reason,
        settings=read_settings(row.settings),
    )


def source_entity(row: ChannelSourceModel) -> Source:
    return Source(
        id=row.id,
        channel_id=row.channel_id,
        pod_id=row.pod_id,
        surface_id=row.surface_id,
        platform=row.platform,
        kind=SourceKind(row.kind),
        external_channel_id=row.external_channel_id,
        external_user_id=row.external_user_id,
        title=row.title,
        last_inbound_at=row.last_inbound_at,
        delivery_ref=row.delivery_ref,
        owner_user_id=row.owner_user_id,
        answers_outsiders=row.answers_outsiders,
        shared_externally=row.shared_externally,
        invite_link=row.invite_link,
        request_id=row.request_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def principal_of(user_id: UUID | None, contact_id: UUID | None) -> Principal | None:
    if user_id is not None:
        return Principal.member(user_id)
    if contact_id is not None:
        return Principal.contact(contact_id)
    return None


def participant_entity(row: ChannelParticipantModel) -> Participant:
    return Participant(
        channel_id=row.channel_id,
        principal=Principal(user_id=row.user_id, contact_id=row.contact_id),
        last_read_at=row.last_read_at,
        notify=NotifyLevel(row.notify),
        notify_via=tuple(row.notify_via) if row.notify_via is not None else None,
    )


def _interaction(row: ChannelMessageModel) -> Interaction | None:
    if row.interaction_kind is None or row.interaction_state is None:
        return None
    return Interaction(
        kind=InteractionKind(row.interaction_kind),
        state=InteractionState(row.interaction_state),
        question=dict(row.interaction or {}),
        awaits=principal_of(row.awaits_user_id, row.awaits_contact_id),
        answer=dict(row.answer) if row.answer is not None else None,
        answered_at=row.answered_at,
        answered_by=principal_of(row.answered_by_user_id, row.answered_by_contact_id),
    )


def message_entity(
    row: ChannelMessageModel,
    *,
    reply_count: int = 0,
    last_reply_at: datetime | None = None,
) -> ChannelMessage:
    return ChannelMessage(
        id=row.id,
        channel_id=row.channel_id,
        pod_id=row.pod_id,
        sender=Sender(
            kind=SenderKind(row.sender_kind),
            user_id=row.sender_user_id,
            contact_id=row.sender_contact_id,
            agent_id=row.sender_agent_id,
            on_behalf_of=principal_of(row.on_behalf_user_id, row.on_behalf_contact_id),
            display_name=row.sender_name,
        ),
        text=row.body,
        created_at=row.created_at,
        source_id=row.source_id,
        members_only=row.members_only,
        conversation_id=row.conversation_id,
        external_message_id=row.external_message_id,
        external_thread_id=row.external_thread_id,
        reply_ref=row.reply_ref,
        parts=tuple(row.parts or ()),
        agent_run_id=row.agent_run_id,
        reply_to_id=row.reply_to_id,
        thread_root_id=row.thread_root_id,
        edited_at=row.edited_at,
        deleted_at=row.deleted_at,
        interaction=_interaction(row),
        reply_count=reply_count,
        last_reply_at=last_reply_at,
    )
