"""Whether a Telegram group message was put to *this* bot.

The parser cannot answer that: an update names the bot only by entities and a
reply's author, and which bot we are is a ``getMe`` away. So the parser records
what it saw and this settles it once the bot's username and id are known.

Pure on purpose, so the rules are testable without Telegram.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent


def addressed_to_this_bot(
    parsed: ParsedInboundSurfaceEvent,
    *,
    bot_username: str,
    bot_user_id: str | None,
) -> ParsedInboundSurfaceEvent:
    """The event with ``mentioned_agent`` settled against this bot's identity.

    Upgraded when the bot is named (an ``@username`` entity, a ``text_mention``
    of its id, or the name typed without an entity) or when the message replies
    to one of its own messages.

    Downgraded when the only reason it looked addressed was a reply to some
    *other* bot. The parser reads any ``reply_to_message.from.is_bot`` as "to
    us", which holds only under privacy mode: a bot that is a group admin, or
    has privacy off, receives every reply, and answered the ones meant for the
    group's other bots.
    """
    metadata = dict(parsed.metadata or {})
    replied_to = str(metadata.get("reply_to_user_id") or "")
    replies_to_us = bool(replied_to) and replied_to == bot_user_id
    if replies_to_us or _names_this_bot(parsed, bot_username, bot_user_id):
        return parsed.model_copy(update={"mentioned_agent": True})
    replies_to_another_bot = (
        bool(replied_to) and bool(bot_user_id) and bool(metadata.get("is_thread_reply"))
    )
    if replies_to_another_bot and not metadata.get("has_bot_command"):
        metadata["is_thread_reply"] = False
        return parsed.model_copy(
            update={"mentioned_agent": False, "metadata": metadata}
        )
    return parsed


def _names_this_bot(
    parsed: ParsedInboundSurfaceEvent, bot_username: str, bot_user_id: str | None
) -> bool:
    """An ``@username`` entity, a ``text_mention`` of its id, or the name typed."""
    metadata = parsed.metadata or {}
    username = bot_username.lower()
    if username and (
        username
        in {str(name).lower() for name in metadata.get("mentioned_usernames") or []}
        or f"@{username}" in (parsed.message_text or "").lower()
    ):
        return True
    return bool(bot_user_id) and bot_user_id in {
        str(uid) for uid in metadata.get("text_mention_user_ids") or []
    }
