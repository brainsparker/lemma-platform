"""A channel's timeline: lines added in order, read by number.

Every line takes the next number in its channel (``seq``) under the channel
row's lock, so numbers are gapless and a reader resumes exactly where it left
off. A line written twice is one line: a client names its line
(``client_message_id``) and a platform names its message (``source_id`` +
``external_message_id``); either finds the line already there.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import Row, Select, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import (
    ChannelMessage,
    Sender,
    SenderKind,
)
from app.modules.channels.domain.parts import Part, dump_parts
from app.modules.channels.infrastructure.access import visible_to
from app.modules.channels.infrastructure.models import (
    ChannelMessageDeliveryModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
    ChannelSourceModel,
)
from app.modules.channels.infrastructure.rows import MAX_LINES, message_entity, now


@dataclass(frozen=True, slots=True)
class NewLine:
    sender: Sender
    text: str
    source_id: UUID | None = None
    members_only: bool = False
    parts: Sequence[Part] = field(default_factory=tuple)
    client_message_id: str | None = None
    conversation_id: UUID | None = None
    agent_run_id: UUID | None = None
    external_message_id: str | None = None
    external_thread_id: str | None = None
    reply_ref: dict[str, object] | None = None
    reply_to_id: UUID | None = None
    thread_root_id: UUID | None = None


def _sender_values(sender: Sender) -> dict[str, object]:
    behalf = sender.on_behalf_of
    return {
        "sender_kind": sender.kind.value,
        "sender_user_id": sender.user_id,
        "sender_contact_id": sender.contact_id,
        "sender_agent_id": sender.agent_id,
        "on_behalf_user_id": behalf.user_id if behalf else None,
        "on_behalf_contact_id": behalf.contact_id if behalf else None,
        "sender_name": sender.display_name[:255] if sender.display_name else None,
    }


def _nameless(row: ChannelMessageModel) -> bool:
    """Said by somebody nobody could name: no member, no contact."""
    return (
        row.sender_kind == SenderKind.CONTACT.value
        and row.sender_contact_id is None
        and row.sender_user_id is None
    )


def _with_question() -> Select[tuple[ChannelMessageModel, ChannelQuestionModel]]:
    return select(ChannelMessageModel, ChannelQuestionModel).outerjoin(
        ChannelQuestionModel,
        ChannelQuestionModel.message_id == ChannelMessageModel.id,
    )


class LineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- writing -------------------------------------------------------------------

    async def append(
        self, channel_id: UUID, line: NewLine
    ) -> tuple[ChannelMessage, bool]:
        """Add a line, or return the one this message already made. ``True``
        only for a line written now."""
        earlier = await self._written_before(channel_id, line)
        if earlier is not None:
            return earlier, False
        head = await self.session.scalar(
            select(ChannelModel).where(ChannelModel.id == channel_id).with_for_update()
        )
        if head is None:
            raise LookupError("No such channel")
        # Under the lock: a writer that took it first may have written it.
        earlier = await self._written_before(channel_id, line)
        if earlier is not None:
            return earlier, False
        seq = head.last_seq + 1
        row = ChannelMessageModel(
            channel_id=channel_id,
            pod_id=head.pod_id,
            seq=seq,
            body=line.text,
            parts=dump_parts(list(line.parts)),
            source_id=line.source_id,
            members_only=line.members_only,
            client_message_id=line.client_message_id,
            conversation_id=line.conversation_id,
            agent_run_id=line.agent_run_id,
            external_message_id=line.external_message_id,
            external_thread_id=line.external_thread_id,
            reply_ref=dict(line.reply_ref) if line.reply_ref is not None else None,
            reply_to_id=line.reply_to_id,
            thread_root_id=line.thread_root_id,
            **_sender_values(line.sender),
        )
        self.session.add(row)
        await self.session.flush()
        head.last_seq = seq
        if line.thread_root_id is None:
            head.last_line_id = row.id
            head.last_line_at = func.greatest(head.last_line_at, row.created_at)
        if line.source_id is not None and line.sender.kind is not SenderKind.AGENT:
            await self.session.execute(
                update(ChannelSourceModel)
                .where(ChannelSourceModel.id == line.source_id)
                .values(last_inbound_at=row.created_at)
            )
        await self.session.flush()
        return message_entity(row), True

    async def _written_before(
        self, channel_id: UUID, line: NewLine
    ) -> ChannelMessage | None:
        message = ChannelMessageModel
        if line.client_message_id is not None:
            row = await self.session.scalar(
                select(message).where(
                    message.channel_id == channel_id,
                    message.client_message_id == line.client_message_id,
                )
            )
            if row is not None:
                return message_entity(row)
        if line.external_message_id is None or line.source_id is None:
            return None
        row = await self.session.scalar(
            select(message).where(
                message.source_id == line.source_id,
                func.md5(message.external_message_id)
                == func.md5(line.external_message_id),
            )
        )
        if row is None:
            return None
        return await self._heard_again(row, line)

    async def _heard_again(
        self, row: ChannelMessageModel, line: NewLine
    ) -> ChannelMessage:
        """A platform message heard twice: the second hearing may know more --
        the conversation answering it, what answers it in place, who said it."""
        if row.conversation_id is None and line.conversation_id is not None:
            row.conversation_id = line.conversation_id
        if row.reply_ref is None and line.reply_ref is not None:
            row.reply_ref = dict(line.reply_ref)
        sender = line.sender
        if _nameless(row) and (sender.user_id or sender.contact_id):
            values = _sender_values(sender)
            values["sender_name"] = row.sender_name or values["sender_name"]
            for key, value in values.items():
                setattr(row, key, value)
        await self.session.flush()
        return message_entity(row)

    async def edit(
        self, message_id: UUID, *, text: str, parts: Sequence[Part] | None = None
    ) -> ChannelMessage | None:
        """Change what a line says, unless it was deleted meanwhile."""
        values: dict[str, object] = {"body": text, "edited_at": now()}
        if parts is not None:
            values["parts"] = dump_parts(list(parts))
        row = await self.session.scalar(
            update(ChannelMessageModel)
            .where(
                ChannelMessageModel.id == message_id,
                ChannelMessageModel.deleted_at.is_(None),
            )
            .values(**values)
            .returning(ChannelMessageModel)
        )
        return message_entity(row) if row is not None else None

    async def delete(self, message_id: UUID) -> ChannelMessage | None:
        """Forget what a line said; it keeps its place in the timeline."""
        row = await self.session.scalar(
            update(ChannelMessageModel)
            .where(
                ChannelMessageModel.id == message_id,
                ChannelMessageModel.deleted_at.is_(None),
            )
            .values(body="", parts=[], reply_ref=None, deleted_at=now())
            .returning(ChannelMessageModel)
        )
        return message_entity(row) if row is not None else None

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

    # -- reading -------------------------------------------------------------------

    async def get(self, message_id: UUID) -> ChannelMessage | None:
        found = (
            await self.session.execute(
                _with_question().where(ChannelMessageModel.id == message_id)
            )
        ).first()
        if found is None:
            return None
        return message_entity(found[0], question=found[1])

    async def by_ids(self, ids: Sequence[UUID]) -> dict[UUID, ChannelMessage]:
        if not ids:
            return {}
        rows = await self.session.execute(
            _with_question().where(ChannelMessageModel.id.in_(list(ids)[:MAX_LINES]))
        )
        return {row[0].id: message_entity(row[0], question=row[1]) for row in rows}

    async def page(
        self,
        channel_id: UUID,
        *,
        limit: int,
        before_seq: int | None = None,
        after_seq: int | None = None,
        source_id: UUID | None = None,
        top_level: bool = True,
    ) -> list[ChannelMessage]:
        """Lines oldest first: the newest page, or those before or after a
        number. ``source_id``: only what was said there (a contact reading)."""
        message = ChannelMessageModel
        statement = _with_question().where(message.channel_id == channel_id)
        if top_level:
            statement = statement.where(message.thread_root_id.is_(None))
        if source_id is not None:
            statement = statement.where(message.source_id == source_id)
        if before_seq is not None:
            statement = statement.where(message.seq < before_seq)
        size = max(1, min(limit, MAX_LINES))
        if after_seq is not None:
            statement = statement.where(message.seq > after_seq)
            rows = list(
                await self.session.execute(statement.order_by(message.seq).limit(size))
            )
        else:
            rows = list(
                await self.session.execute(
                    statement.order_by(message.seq.desc()).limit(size)
                )
            )
            rows.reverse()
        return await self._with_replies(rows)

    async def thread(
        self, root_id: UUID, *, limit: int, after_seq: int | None = None
    ) -> list[ChannelMessage]:
        message = ChannelMessageModel
        statement = _with_question().where(message.thread_root_id == root_id)
        if after_seq is not None:
            statement = statement.where(message.seq > after_seq)
        rows = await self.session.execute(
            statement.order_by(message.seq).limit(max(1, min(limit, MAX_LINES)))
        )
        return [message_entity(row[0], question=row[1]) for row in rows]

    async def _with_replies(
        self,
        rows: Sequence[Row[tuple[ChannelMessageModel, ChannelQuestionModel | None]]],
    ) -> list[ChannelMessage]:
        roots = [row[0].id for row in rows]
        counts: dict[UUID, tuple[int, datetime | None]] = {}
        if roots:
            found = await self.session.execute(
                select(
                    ChannelMessageModel.thread_root_id,
                    func.count(),
                    func.max(ChannelMessageModel.created_at),
                )
                .where(ChannelMessageModel.thread_root_id.in_(roots))
                .group_by(ChannelMessageModel.thread_root_id)
            )
            counts = {row[0]: (int(row[1]), row[2]) for row in found}
        return [
            message_entity(
                row[0],
                question=row[1],
                reply_count=counts.get(row[0].id, (0, None))[0],
                last_reply_at=counts.get(row[0].id, (0, None))[1],
            )
            for row in rows
        ]

    async def last_heard(self, conversation_id: UUID) -> ChannelMessage | None:
        """The latest line a person said in a conversation, wherever they said it."""
        row = await self.session.scalar(
            select(ChannelMessageModel)
            .where(
                ChannelMessageModel.conversation_id == conversation_id,
                ChannelMessageModel.sender_kind != SenderKind.AGENT.value,
            )
            .order_by(ChannelMessageModel.seq.desc())
            .limit(1)
        )
        return message_entity(row) if row is not None else None

    async def at(self, source_id: UUID, external_message_id: str) -> UUID | None:
        """The line a platform message is at this source, if it is one: heard
        there, or written in Lemma and delivered there as that message."""
        heard = await self.session.scalar(
            select(ChannelMessageModel.id).where(
                ChannelMessageModel.source_id == source_id,
                func.md5(ChannelMessageModel.external_message_id)
                == func.md5(external_message_id),
            )
        )
        if heard is not None:
            return heard
        return await self.session.scalar(
            select(ChannelMessageDeliveryModel.message_id)
            .where(
                ChannelMessageDeliveryModel.source_id == source_id,
                ChannelMessageDeliveryModel.external_message_id.is_not(None),
                func.md5(ChannelMessageDeliveryModel.external_message_id)
                == func.md5(external_message_id),
            )
            .limit(1)
        )

    async def search(
        self,
        viewer: Viewer,
        *,
        query: str,
        limit: int,
        channel_id: UUID | None = None,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[ChannelMessage]:
        """Lines matching every word, newest first, in channels ``viewer`` may
        see; notes to members are members' to find. Paged by ``before``."""
        message = ChannelMessageModel
        statement = (
            _with_question()
            .join(ChannelModel, ChannelModel.id == message.channel_id)
            .where(
                visible_to(viewer),
                ChannelModel.archived_at.is_(None),
                message.deleted_at.is_(None),
                message.search.op("@@")(func.plainto_tsquery("simple", query)),
            )
        )
        if channel_id is not None:
            statement = statement.where(message.channel_id == channel_id)
        if before is not None:
            at, message_id = before
            statement = statement.where(
                or_(
                    message.created_at < at,
                    and_(message.created_at == at, message.id < message_id),
                )
            )
        rows = await self.session.execute(
            statement.order_by(message.created_at.desc(), message.id.desc()).limit(
                max(1, min(limit, MAX_LINES))
            )
        )
        return [message_entity(row[0], question=row[1]) for row in rows]
