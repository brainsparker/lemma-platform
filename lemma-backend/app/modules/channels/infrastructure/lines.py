"""A channel's lines: writing them, reading them back, threads and questions.

A line is what people saw. Editing changes what it says and marks it edited;
deleting keeps the row -- a thread, a reply, an answer may hang from it -- and
forgets what it said. A question a line puts to one person is answered once:
the answer is taken only while the question is still open, so two answers
racing each other settle on the first.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import (
    Channel,
    ChannelMessage,
    InteractionKind,
    InteractionState,
    Principal,
    Sender,
    SenderKind,
)
from app.modules.channels.infrastructure.models import (
    ChannelMessageModel,
    ChannelModel,
    ChannelSourceModel,
)
from app.modules.channels.infrastructure.rows import MAX_LINES, message_entity, now

#: The most open questions one read of somebody's "waiting" returns.
MAX_WAITING = 100


@dataclass(frozen=True, slots=True)
class NewLine:
    """Everything a line may carry beyond who said what where."""

    members_only: bool = False
    conversation_id: UUID | None = None
    external_message_id: str | None = None
    external_thread_id: str | None = None
    reply_ref: Mapping[str, object] | None = None
    parts: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    agent_run_id: UUID | None = None
    reply_to_id: UUID | None = None
    thread_root_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NewQuestion:
    """A question a line puts to one person.

    ``question`` carries a ``ref`` -- what an answer resolves: a paused tool
    call, a workflow's wait, a message an agent sent -- which the asking side
    reads back and nothing here interprets.
    """

    kind: InteractionKind
    question: Mapping[str, object]
    awaits: Principal


def _line_values(
    channel: Channel,
    sender: Sender,
    text: str,
    source_id: UUID | None,
    line: NewLine,
    question: NewQuestion | None,
) -> dict[str, object]:
    behalf = sender.on_behalf_of
    values: dict[str, object] = {
        "channel_id": channel.id,
        "pod_id": channel.pod_id,
        "source_id": source_id,
        "sender_kind": sender.kind.value,
        "sender_user_id": sender.user_id,
        "sender_contact_id": sender.contact_id,
        "sender_agent_id": sender.agent_id,
        "on_behalf_user_id": behalf.user_id if behalf else None,
        "on_behalf_contact_id": behalf.contact_id if behalf else None,
        "sender_name": sender.display_name[:255] if sender.display_name else None,
        "body": text,
        "members_only": line.members_only,
        "conversation_id": line.conversation_id,
        "external_message_id": line.external_message_id,
        "external_thread_id": line.external_thread_id,
        "reply_ref": dict(line.reply_ref) if line.reply_ref is not None else None,
        "parts": [dict(part) for part in line.parts] or None,
        "agent_run_id": line.agent_run_id,
        "reply_to_id": line.reply_to_id,
        "thread_root_id": line.thread_root_id,
    }
    if question is not None:
        values |= {
            "interaction_kind": question.kind.value,
            "interaction": dict(question.question),
            "interaction_state": InteractionState.OPEN.value,
            "awaits_user_id": question.awaits.user_id,
            "awaits_contact_id": question.awaits.contact_id,
        }
    return values


def _before(at_and_id: tuple[datetime, UUID] | None) -> list[ColumnElement[bool]]:
    if at_and_id is None:
        return []
    at, message_id = at_and_id
    return [
        or_(
            ChannelMessageModel.created_at < at,
            and_(
                ChannelMessageModel.created_at == at,
                ChannelMessageModel.id < message_id,
            ),
        )
    ]


#: A timeline read pages back from the newest line.
_NEWEST_FIRST = (ChannelMessageModel.created_at.desc(), ChannelMessageModel.id.desc())


def _page(limit: int) -> int:
    return max(1, min(limit, MAX_LINES))


class LineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- writing --------------------------------------------------------------

    async def append(
        self,
        *,
        channel: Channel,
        sender: Sender,
        text: str,
        source_id: UUID | None,
        line: NewLine | None = None,
        question: NewQuestion | None = None,
    ) -> tuple[ChannelMessage, bool]:
        """Add a line, or return the one this platform message already made.

        A platform that delivers a message twice -- a retried webhook, a
        replayed poll -- names it the same both times, and the second writes
        nothing. Says which: ``True`` only for a line written now. A line
        logged before anyone answered it -- a group's -- takes the conversation
        that answers it, and what answers it in place, the second time.
        """
        line = line or NewLine()
        statement = insert(ChannelMessageModel).values(
            **_line_values(channel, sender, text, source_id, line, question)
        )
        if line.external_message_id is not None and source_id is not None:
            statement = statement.on_conflict_do_nothing(
                index_elements=["source_id", "external_message_id"],
                index_where=ChannelMessageModel.external_message_id.is_not(None),
            )
        inserted = await self.session.scalar(statement.returning(ChannelMessageModel))
        if inserted is None:
            return await self._heard_before(source_id, line), False
        await self.session.execute(
            update(ChannelModel)
            .where(ChannelModel.id == channel.id)
            .values(last_message_at=inserted.created_at)
        )
        if source_id is not None and sender.kind is not SenderKind.AGENT:
            await self.session.execute(
                update(ChannelSourceModel)
                .where(ChannelSourceModel.id == source_id)
                .values(last_inbound_at=inserted.created_at)
            )
        return message_entity(inserted), True

    async def _heard_before(
        self, source_id: UUID | None, line: NewLine
    ) -> ChannelMessage:
        existing = await self.session.scalar(
            select(ChannelMessageModel).where(
                ChannelMessageModel.source_id == source_id,
                ChannelMessageModel.external_message_id == line.external_message_id,
            )
        )
        if existing is None:  # pragma: no cover - the conflict named it
            raise LookupError("A duplicate line vanished")
        if existing.conversation_id is None and line.conversation_id is not None:
            existing.conversation_id = line.conversation_id
        if existing.reply_ref is None and line.reply_ref is not None:
            existing.reply_ref = dict(line.reply_ref)
        await self.session.flush()
        return message_entity(existing)

    async def edit(self, message_id: UUID, *, text: str) -> ChannelMessage | None:
        row = await self.session.get(ChannelMessageModel, message_id)
        if row is None or row.deleted_at is not None:
            return None
        row.body = text
        row.edited_at = now()
        await self.session.flush()
        return message_entity(row)

    async def delete(self, message_id: UUID) -> ChannelMessage | None:
        """Forget what a line said, and keep its place in the timeline."""
        row = await self.session.get(ChannelMessageModel, message_id)
        if row is None:
            return None
        if row.deleted_at is None:
            row.body = ""
            row.parts = None
            row.deleted_at = now()
            await self.session.flush()
        return message_entity(row)

    async def note_delivered_as(
        self, message_id: UUID, *, external_message_id: str | None
    ) -> None:
        """The platform's id for a line written in Lemma and delivered there,
        unless the line already has one."""
        if external_message_id is None:
            return
        await self.session.execute(
            update(ChannelMessageModel)
            .where(
                ChannelMessageModel.id == message_id,
                ChannelMessageModel.external_message_id.is_(None),
            )
            .values(external_message_id=external_message_id[:255])
        )

    # -- reading --------------------------------------------------------------

    async def claim(self, message_id: UUID, *, conversation_id: UUID) -> None:
        """The conversation answering a line, once one is: the first claims it."""
        await self.session.execute(
            update(ChannelMessageModel)
            .where(
                ChannelMessageModel.id == message_id,
                ChannelMessageModel.conversation_id.is_(None),
            )
            .values(conversation_id=conversation_id)
        )

    async def last_heard(self, conversation_id: UUID) -> ChannelMessage | None:
        """The latest line a person said in a conversation, wherever they said it."""
        row = await self.session.scalar(
            select(ChannelMessageModel)
            .where(
                ChannelMessageModel.conversation_id == conversation_id,
                ChannelMessageModel.sender_kind != SenderKind.AGENT.value,
            )
            .order_by(*_NEWEST_FIRST)
            .limit(1)
        )
        return message_entity(row) if row is not None else None

    async def get(self, message_id: UUID) -> ChannelMessage | None:
        row = await self.session.get(ChannelMessageModel, message_id)
        return message_entity(row) if row else None

    async def recent(
        self,
        channel_id: UUID,
        *,
        limit: int,
        before: tuple[datetime, UUID] | None = None,
        source_id: UUID | None = None,
        top_level: bool = False,
    ) -> list[ChannelMessage]:
        """The newest lines, oldest first.

        ``source_id`` keeps one source's. ``top_level`` leaves thread replies
        out and counts them on the line they hang from, as a channel's
        timeline shows them.
        """
        conditions: list[ColumnElement[bool]] = [
            ChannelMessageModel.channel_id == channel_id,
            *_before(before),
        ]
        if source_id is not None:
            conditions.append(ChannelMessageModel.source_id == source_id)
        if top_level:
            conditions.append(ChannelMessageModel.thread_root_id.is_(None))
        rows = list(
            await self.session.scalars(
                select(ChannelMessageModel)
                .where(*conditions)
                .order_by(*_NEWEST_FIRST)
                .limit(_page(limit))
            )
        )
        rows.reverse()
        if not top_level:
            return [message_entity(row) for row in rows]
        replies = await self._replies_to([row.id for row in rows])
        return [
            message_entity(
                row,
                reply_count=replies.get(row.id, (0, None))[0],
                last_reply_at=replies.get(row.id, (0, None))[1],
            )
            for row in rows
        ]

    async def latest_in(
        self, channel_ids: Sequence[UUID]
    ) -> dict[UUID, ChannelMessage]:
        """Each channel's newest line outside a thread, in one read."""
        if not channel_ids:
            return {}
        rows = await self.session.scalars(
            select(ChannelMessageModel)
            .where(
                ChannelMessageModel.channel_id.in_(list(channel_ids)[:MAX_LINES]),
                ChannelMessageModel.thread_root_id.is_(None),
            )
            .order_by(
                ChannelMessageModel.channel_id,
                ChannelMessageModel.created_at.desc(),
                ChannelMessageModel.id.desc(),
            )
            .distinct(ChannelMessageModel.channel_id)
        )
        return {row.channel_id: message_entity(row) for row in rows}

    async def _replies_to(
        self, root_ids: list[UUID]
    ) -> dict[UUID, tuple[int, datetime | None]]:
        if not root_ids:
            return {}
        rows = await self.session.execute(
            select(
                ChannelMessageModel.thread_root_id,
                func.count(),
                func.max(ChannelMessageModel.created_at),
            )
            .where(ChannelMessageModel.thread_root_id.in_(root_ids))
            .group_by(ChannelMessageModel.thread_root_id)
        )
        return {root: (count, last) for root, count, last in rows.tuples()}

    async def thread(
        self,
        root_id: UUID,
        *,
        limit: int,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[ChannelMessage]:
        """A thread's replies, oldest first -- the newest page of them."""
        rows = list(
            await self.session.scalars(
                select(ChannelMessageModel)
                .where(ChannelMessageModel.thread_root_id == root_id, *_before(before))
                .order_by(*_NEWEST_FIRST)
                .limit(_page(limit))
            )
        )
        return [message_entity(row) for row in reversed(rows)]

    async def search(
        self, *, channel_ids: list[UUID], words: str, limit: int
    ) -> list[ChannelMessage]:
        """Lines in these channels matching every word, newest first."""
        if not channel_ids or not words.strip():
            return []
        query = func.plainto_tsquery("simple", words)
        rows = await self.session.scalars(
            select(ChannelMessageModel)
            .where(
                ChannelMessageModel.channel_id.in_(channel_ids),
                ChannelMessageModel.deleted_at.is_(None),
                func.to_tsvector("simple", ChannelMessageModel.body).op("@@")(query),
            )
            .order_by(ChannelMessageModel.created_at.desc())
            .limit(_page(limit))
        )
        return [message_entity(row) for row in rows]

    # -- questions ------------------------------------------------------------

    async def answer(
        self,
        message_id: UUID,
        *,
        answer: Mapping[str, object],
        by: Principal,
    ) -> ChannelMessage | None:
        """Take the answer to an open question; ``None`` once it is not open."""
        row = await self.session.scalar(
            update(ChannelMessageModel)
            .where(
                ChannelMessageModel.id == message_id,
                ChannelMessageModel.interaction_state == InteractionState.OPEN.value,
            )
            .values(
                interaction_state=InteractionState.ANSWERED.value,
                answer=dict(answer),
                answered_at=now(),
                answered_by_user_id=by.user_id,
                answered_by_contact_id=by.contact_id,
            )
            .returning(ChannelMessageModel)
        )
        return message_entity(row) if row is not None else None

    async def close_question(
        self,
        *,
        ref: str,
        state: InteractionState,
        answer: Mapping[str, object] | None = None,
        by: Principal | None = None,
    ) -> list[ChannelMessage]:
        """End the open questions ``ref`` names -- answered somewhere else, its
        run stopped, its time run out."""
        values: dict[str, object] = {"interaction_state": state.value}
        if state is InteractionState.ANSWERED:
            values |= {
                "answer": dict(answer) if answer is not None else None,
                "answered_at": now(),
                "answered_by_user_id": by.user_id if by else None,
                "answered_by_contact_id": by.contact_id if by else None,
            }
        rows = await self.session.scalars(
            update(ChannelMessageModel)
            .where(
                ChannelMessageModel.interaction_state == InteractionState.OPEN.value,
                ChannelMessageModel.interaction["ref"].astext == ref,
            )
            .values(**values)
            .returning(ChannelMessageModel)
        )
        return [message_entity(row) for row in rows]

    async def open_questions_of(
        self, *, conversation_id: UUID, limit: int = MAX_WAITING
    ) -> list[ChannelMessage]:
        """A conversation's questions still waiting for an answer."""
        rows = await self.session.scalars(
            select(ChannelMessageModel)
            .where(
                ChannelMessageModel.conversation_id == conversation_id,
                ChannelMessageModel.interaction_state == InteractionState.OPEN.value,
            )
            .order_by(ChannelMessageModel.created_at)
            .limit(max(1, min(limit, MAX_WAITING)))
        )
        return [message_entity(row) for row in rows]

    async def question(self, ref: str) -> ChannelMessage | None:
        """The newest line asking the question ``ref`` names."""
        row = await self.session.scalar(
            select(ChannelMessageModel)
            .where(
                ChannelMessageModel.interaction_kind.is_not(None),
                ChannelMessageModel.interaction["ref"].astext == ref,
            )
            .order_by(ChannelMessageModel.created_at.desc())
            .limit(1)
        )
        return message_entity(row) if row is not None else None

    async def waiting_on(
        self, *, user_id: UUID, pod_id: UUID, limit: int = MAX_WAITING
    ) -> list[ChannelMessage]:
        """The open questions put to one member in a pod, newest first."""
        rows = await self.session.scalars(
            select(ChannelMessageModel)
            .where(
                ChannelMessageModel.awaits_user_id == user_id,
                ChannelMessageModel.pod_id == pod_id,
                ChannelMessageModel.interaction_state == InteractionState.OPEN.value,
            )
            .order_by(ChannelMessageModel.created_at.desc())
            .limit(max(1, min(limit, MAX_WAITING)))
        )
        return [message_entity(row) for row in rows]
