"""Rows to entities, in one place, so every repository reads them the same way."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.modules.channels.domain.entities import (
    AgentAnswers,
    Channel,
    ChannelKind,
    ChannelMessage,
    ContactStatus,
    Delivery,
    DeliveryStatus,
    ExternalAccount,
    Member,
    MemberRole,
    NotifyLevel,
    Principal,
    Question,
    QuestionKind,
    QuestionState,
    Sender,
    SenderKind,
    Source,
    SourceKind,
    Visibility,
)
from app.modules.channels.domain.parts import read_parts
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMessageDeliveryModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
    ChannelSourceModel,
    ExternalAccountModel,
)

#: The most rows any one read returns.
MAX_LINES = 200


def now() -> datetime:
    return datetime.now(timezone.utc)


def account_entity(row: ExternalAccountModel) -> ExternalAccount:
    return ExternalAccount(
        id=row.id,
        pod_id=row.pod_id,
        platform=row.platform,
        account_key=row.account_key,
        surface_id=row.surface_id,
    )


def channel_entity(row: ChannelModel) -> Channel:
    return Channel(
        id=row.id,
        pod_id=row.pod_id,
        kind=ChannelKind(row.kind),
        visibility=Visibility(row.visibility),
        title=row.title,
        topic=row.topic,
        member_user_id=row.member_user_id,
        contact_id=row.contact_id,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        archived_at=row.archived_at,
        agents_answer=row.agents_answer,
        hand_back_after_hours=row.hand_back_after_hours,
        assignee_user_id=row.assignee_user_id,
        assigned_at=row.assigned_at,
        assignee_last_line_at=row.assignee_last_line_at,
        status=ContactStatus(row.status),
        attention_at=row.attention_at,
        attention_reason=row.attention_reason,
        acknowledged_at=row.acknowledged_at,
        last_seq=row.last_seq,
        last_line_id=row.last_line_id,
        last_line_at=row.last_line_at,
    )


def source_entity(row: ChannelSourceModel, *, surface_id: UUID | None = None) -> Source:
    """``surface_id`` is the account's: the installation it is reached by now."""
    return Source(
        id=row.id,
        channel_id=row.channel_id,
        pod_id=row.pod_id,
        account_id=row.account_id,
        surface_id=surface_id,
        platform=row.platform,
        kind=SourceKind(row.kind),
        is_private=row.is_private,
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


def member_entity(row: ChannelMemberModel) -> Member:
    return Member(
        channel_id=row.channel_id,
        user_id=row.user_id,
        contact_id=row.contact_id,
        agent_id=row.agent_id,
        role=MemberRole(row.role),
        notify=NotifyLevel(row.notify),
        notify_via=tuple(row.notify_via) if row.notify_via is not None else None,
        muted_until=row.muted_until,
        answers=AgentAnswers(row.answers) if row.answers else None,
        joined_at=row.created_at,
        seat_id=row.id,
    )


def principal_of(user_id: UUID | None, contact_id: UUID | None) -> Principal | None:
    if user_id is not None:
        return Principal.member(user_id)
    if contact_id is not None:
        return Principal.contact(contact_id)
    return None


def sender_of(row: ChannelMessageModel) -> Sender:
    return Sender(
        kind=SenderKind(row.sender_kind),
        user_id=row.sender_user_id,
        contact_id=row.sender_contact_id,
        agent_id=row.sender_agent_id,
        on_behalf_of=principal_of(row.on_behalf_user_id, row.on_behalf_contact_id),
        display_name=row.sender_name,
    )


def question_entity(row: ChannelQuestionModel) -> Question:
    awaits = principal_of(row.awaits_user_id, row.awaits_contact_id)
    assert awaits is not None  # a CHECK holds it to exactly one
    return Question(
        message_id=row.message_id,
        channel_id=row.channel_id,
        pod_id=row.pod_id,
        ref=row.ref,
        batch_id=row.batch_id,
        kind=QuestionKind(row.kind),
        state=QuestionState(row.state),
        question=row.question,
        awaits=awaits,
        answer=row.answer,
        answered_by=principal_of(row.answered_by_user_id, row.answered_by_contact_id),
        answered_at=row.answered_at,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


def message_entity(
    row: ChannelMessageModel,
    *,
    question: ChannelQuestionModel | None = None,
    reply_count: int = 0,
    last_reply_at: datetime | None = None,
) -> ChannelMessage:
    return ChannelMessage(
        id=row.id,
        channel_id=row.channel_id,
        pod_id=row.pod_id,
        seq=row.seq,
        sender=sender_of(row),
        text=row.body,
        created_at=row.created_at,
        client_message_id=row.client_message_id,
        source_id=row.source_id,
        target_source_id=row.target_source_id,
        members_only=row.members_only,
        parts=read_parts(row.parts),
        conversation_id=row.conversation_id,
        agent_run_id=row.agent_run_id,
        external_message_id=row.external_message_id,
        external_thread_id=row.external_thread_id,
        reply_ref=row.reply_ref,
        reply_to_id=row.reply_to_id,
        thread_root_id=row.thread_root_id,
        edited_at=row.edited_at,
        deleted_at=row.deleted_at,
        question=question_entity(question) if question is not None else None,
        reply_count=reply_count,
        last_reply_at=last_reply_at,
    )


def delivery_entity(row: ChannelMessageDeliveryModel) -> Delivery:
    return Delivery(
        id=row.id,
        message_id=row.message_id,
        pod_id=row.pod_id,
        source_id=row.source_id,
        recipient_user_id=row.recipient_user_id,
        platform=row.platform,
        status=DeliveryStatus(row.status),
        reason=row.reason,
        external_message_id=row.external_message_id,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        claimed_at=row.claimed_at,
        sent_at=row.sent_at,
        created_at=row.created_at,
    )
