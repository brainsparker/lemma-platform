"""Full-text search over the lines a member may read, newest first.

A common word is in a large share of a pod's lines. Asked for "the newest
page of every match" with nothing to walk in time order, Postgres finds all
of them, reads each and sorts them, to show a page. With the pod's lines
indexed by when they were said (``ix_channel_messages_pod_time``) it walks
back from the newest instead, testing each line against the words, and stops
when the page is full; a rare word is still found through its own short
posting list in the text index (``ix_channel_messages_search``). The planner
picks between the two by how common the words are; ``LIMIT`` caps what
either reads. Within one channel, the channel's own index narrows the
matches to that channel's.

What is found is what the reader may see and reads in full -- an answer
withheld from them is not found by what it said -- filtered in the same
statement, before the page is cut.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import ChannelKind, ChannelMessage, SenderKind
from app.modules.channels.infrastructure.access import shown_in_full_to, visible_to
from app.modules.channels.infrastructure.models import (
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
)
from app.modules.channels.infrastructure.rows import MAX_LINES, message_entity

#: A snippet is the text around the match, unmarked: a reader highlights the
#: words itself, so nobody's line is ever rendered as markup.
SNIPPET_OPTIONS = "StartSel='',StopSel='',MaxWords=24,MinWords=8,MaxFragments=1"


@dataclass(frozen=True, slots=True)
class SpeakerRef:
    kind: SenderKind
    id: UUID


@dataclass(frozen=True, slots=True)
class SearchFilter:
    """What a search is narrowed to; every field absent is no narrowing."""

    channel_id: UUID | None = None
    kind: ChannelKind | None = None
    #: Who said it: a member, a contact, or an agent.
    sender: SpeakerRef | None = None
    #: Said at or after this; before ``until``.
    after: datetime | None = None
    until: datetime | None = None
    #: Archived channels are searched too.
    archived: bool = False


@dataclass(frozen=True, slots=True)
class SearchHit:
    message: ChannelMessage
    #: ``ts_rank`` of the line against the words: higher is closer.
    rank: float
    snippet: str | None


class LineSearch:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(
        self,
        viewer: Viewer,
        *,
        query: str,
        limit: int,
        within: SearchFilter | None = None,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[SearchHit]:
        """Lines matching every word, newest first, in channels ``viewer`` may
        see and lines they read in full. Notes to members are members' to
        find. Paged by ``before`` (a hit's time and id)."""
        within = within or SearchFilter()
        rows = await self.session.execute(
            self._matching(
                viewer, query, within, before, limit=max(1, min(limit, MAX_LINES))
            )
        )
        return [
            SearchHit(
                message=message_entity(row[0], question=row[1]),
                rank=float(row[2] or 0.0),
                snippet=str(row[3]) if row[3] is not None else None,
            )
            for row in rows
        ]

    @staticmethod
    def _matching(
        viewer: Viewer,
        query: str,
        within: SearchFilter,
        before: tuple[datetime, UUID] | None,
        *,
        limit: int,
    ) -> Select[tuple[ChannelMessageModel, ChannelQuestionModel, float, str]]:
        message = ChannelMessageModel
        words = func.plainto_tsquery("simple", query)
        statement = (
            select(
                message,
                ChannelQuestionModel,
                func.ts_rank(message.search, words).label("rank"),
                # Costly, so Postgres makes it after the page is cut: only for
                # the lines shown, from the row already read.
                func.ts_headline("simple", message.body, words, SNIPPET_OPTIONS),
            )
            .outerjoin(
                ChannelQuestionModel, ChannelQuestionModel.message_id == message.id
            )
            .join(ChannelModel, ChannelModel.id == message.channel_id)
            .where(
                # The pod beside the words: one GIN (``btree_gin``) finds both.
                message.pod_id == viewer.pod_id,
                message.search.op("@@")(words),
                visible_to(viewer),
                shown_in_full_to(viewer),
                message.deleted_at.is_(None),
                *_narrowed(within),
            )
        )
        if before is not None:
            at, message_id = before
            statement = statement.where(
                or_(
                    message.created_at < at,
                    and_(message.created_at == at, message.id < message_id),
                )
            )
        return statement.order_by(message.created_at.desc(), message.id.desc()).limit(
            limit
        )


def _narrowed(within: SearchFilter) -> list[ColumnElement[bool]]:
    message = ChannelMessageModel
    found: list[ColumnElement[bool]] = []
    if not within.archived:
        found.append(ChannelModel.archived_at.is_(None))
    if within.channel_id is not None:
        found.append(message.channel_id == within.channel_id)
    if within.kind is not None:
        found.append(ChannelModel.kind == within.kind.value)
    if within.sender is not None:
        found += _said_by(within.sender)
    if within.after is not None:
        found.append(message.created_at >= within.after)
    if within.until is not None:
        found.append(message.created_at < within.until)
    return found


def _said_by(sender: SpeakerRef) -> list[ColumnElement[bool]]:
    message = ChannelMessageModel
    column = {
        SenderKind.MEMBER: message.sender_user_id,
        SenderKind.CONTACT: message.sender_contact_id,
        SenderKind.AGENT: message.sender_agent_id,
    }[sender.kind]
    return [message.sender_kind == sender.kind.value, column == sender.id]


__all__ = [
    "SNIPPET_OPTIONS",
    "LineSearch",
    "SearchFilter",
    "SearchHit",
    "SpeakerRef",
]
