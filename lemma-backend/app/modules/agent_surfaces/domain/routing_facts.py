"""The parts of an inbound event that decide whether it is admitted, and where
a reply to it goes -- read as types rather than as mapping keys.

`ParsedInboundSurfaceEvent.metadata` and `.reply_target` are stored mappings:
they ride in job payloads and in a conversation link's last event, so their
shape is a stored contract and is not changed here. What was untyped was the
reading. Admission asked `metadata.get("is_thread_reply")` in four modules and
a platform's mention rules in two more, each spelling its own keys; a reply
read `reply_target.get("phone_number_id")` wherever it needed it. These views
are read once from the mapping, so a misspelt key is an attribute error rather
than a silent ``None``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _texts(value: object) -> frozenset[str]:
    if isinstance(value, list | tuple | set | frozenset):
        return frozenset(str(item) for item in value if item is not None)
    return frozenset()


@dataclass(frozen=True, slots=True)
class InboundRoutingFacts:
    """What the parser saw that decides whether a group message is for us."""

    #: A reply inside a thread. Says "a reply", never "a reply to us": whose
    #: thread it is is the conversation link's to answer.
    is_thread_reply: bool = False
    #: The platform's own event type, where it has several (Slack's
    #: ``app_mention`` against a plain ``message``).
    event_type: str | None = None
    #: Slack's conversation type (``mpim`` is a group DM).
    channel_type: str | None = None
    #: Platform user ids @-mentioned in the message.
    mentioned_user_ids: frozenset[str] = frozenset()
    #: @usernames mentioned as text entities (Telegram).
    mentioned_usernames: frozenset[str] = frozenset()
    #: Users mentioned by id without a username (Telegram ``text_mention``).
    text_mention_user_ids: frozenset[str] = frozenset()
    #: The author of the message this one replies to (Telegram).
    reply_to_user_id: str | None = None
    #: The message is a /command (Telegram).
    has_bot_command: bool = False

    @classmethod
    def of(cls, event: ParsedInboundSurfaceEvent) -> Self:
        metadata: Mapping[str, object] = event.metadata or {}
        return cls(
            is_thread_reply=bool(metadata.get("is_thread_reply")),
            event_type=_text(metadata.get("event_type")),
            channel_type=_text(metadata.get("channel_type")),
            mentioned_user_ids=_texts(metadata.get("mentioned_user_ids")),
            mentioned_usernames=_texts(metadata.get("mentioned_usernames")),
            text_mention_user_ids=_texts(metadata.get("text_mention_user_ids")),
            reply_to_user_id=_text(metadata.get("reply_to_user_id")),
            has_bot_command=bool(metadata.get("has_bot_command")),
        )


@dataclass(frozen=True, slots=True)
class ReplyTarget:
    """Where a reply to an event goes, on whichever platform it came from.

    One record rather than one per platform: every platform's reply target is
    a few of these strings, and which ones is the platform adapter's business.
    """

    #: Slack's channel; Telegram's chat; WhatsApp's recipient.
    channel: str | None = None
    #: Slack's thread ``ts``.
    thread_ts: str | None = None
    #: Telegram's chat id.
    chat_id: str | None = None
    #: The message being answered, where the platform can quote it.
    message_id: str | None = None
    #: Telegram's forum topic.
    message_thread_id: str | None = None
    #: Teams' Bot Framework endpoint for this conversation.
    service_url: str | None = None
    #: Teams' conversation id.
    conversation_id: str | None = None
    #: The WhatsApp number the message arrived on.
    phone_number_id: str | None = None
    #: Email: who the reply goes to.
    recipient_email: str | None = None

    @classmethod
    def of(cls, event: ParsedInboundSurfaceEvent | None) -> Self:
        target: Mapping[str, object] = (event.reply_target if event else None) or {}
        return cls(
            channel=_text(target.get("channel")),
            thread_ts=_text(target.get("thread_ts")),
            chat_id=_text(target.get("chat_id")),
            message_id=_text(target.get("message_id")),
            message_thread_id=_text(target.get("message_thread_id")),
            service_url=_text(target.get("service_url")),
            conversation_id=_text(target.get("conversation_id")),
            phone_number_id=_text(target.get("phone_number_id")),
            recipient_email=_text(target.get("recipient_email")),
        )


__all__ = ["InboundRoutingFacts", "ReplyTarget"]
