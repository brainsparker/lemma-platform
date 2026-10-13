"""Where an approval card was posted, as a platform can find it again."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DecisionCardHandle(BaseModel):
    """One posted card: the chat it is in and the message that is it."""

    platform: str
    channel: str
    message_id: str
    #: For a platform that rewrites a message whole: the card's own blocks, to
    #: rewrite it without its buttons. Telegram edits the keyboard alone.
    blocks: list[dict[str, object]] = Field(default_factory=list)


__all__ = ["DecisionCardHandle"]
