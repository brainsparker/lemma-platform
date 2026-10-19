"""Questions lines put to one person: asked, claimed, answered, closed.

An answer is taken in two steps so two people -- or a tap and a reply --
cannot both answer: ``claim`` moves OPEN to ANSWERING for one caller, the
asker takes the answer, and ``settle`` records the answer that took effect
(or ``release`` puts it back to OPEN when the asker refused it). A claim
older than ``CLAIM_TIMEOUT`` was abandoned by a caller that died, and may be
taken again. Every lookup is by pod: a ref is only unique within one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import (
    Principal,
    Question,
    QuestionKind,
    QuestionState,
)
from app.modules.channels.infrastructure.access import visible_to
from app.modules.channels.infrastructure.models import (
    ChannelModel,
    ChannelQuestionModel,
)
from app.modules.channels.infrastructure.rows import MAX_LINES, now, question_entity

#: How long one caller may hold a question while its asker takes the answer.
CLAIM_TIMEOUT = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class NewQuestion:
    kind: QuestionKind
    ref: str
    question: Mapping[str, object]
    awaits: Principal
    batch_id: str | None = None
    expires_at: datetime | None = None


def _escape_like(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class QuestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ask(
        self, *, message_id: UUID, channel_id: UUID, pod_id: UUID, new: NewQuestion
    ) -> Question:
        """The line asks this; a second ask with the same ref is the first."""
        q = ChannelQuestionModel
        await self.session.execute(
            insert(q)
            .values(
                message_id=message_id,
                channel_id=channel_id,
                pod_id=pod_id,
                ref=new.ref,
                batch_id=new.batch_id,
                kind=new.kind.value,
                question=dict(new.question),
                awaits_user_id=new.awaits.user_id,
                awaits_contact_id=new.awaits.contact_id,
                expires_at=new.expires_at,
            )
            .on_conflict_do_nothing(constraint="uq_channel_questions_ref")
        )
        found = await self.by_ref(pod_id=pod_id, ref=new.ref)
        assert found is not None
        if found.message_id != message_id:
            # A ref is asked once in a pod: a retried post is the same line
            # (``client_message_id``), so another line asking it is a bug in
            # whoever asked -- not a question to hand back silently.
            raise ValueError(f"Question {new.ref!r} was asked by another line")
        return found

    async def by_ref(self, *, pod_id: UUID, ref: str) -> Question | None:
        row = await self.session.scalar(
            select(ChannelQuestionModel).where(
                ChannelQuestionModel.pod_id == pod_id, ChannelQuestionModel.ref == ref
            )
        )
        return question_entity(row) if row else None

    async def of_message(self, message_id: UUID) -> Question | None:
        row = await self.session.get(ChannelQuestionModel, message_id)
        return question_entity(row) if row else None

    # -- answering ------------------------------------------------------------------

    async def claim(self, message_id: UUID, *, at: datetime) -> Question | None:
        """Take the question to answer it; ``None`` if somebody else has it or
        it is closed."""
        q = ChannelQuestionModel
        row = await self.session.scalar(
            update(q)
            .where(
                q.message_id == message_id,
                or_(
                    q.state == QuestionState.OPEN.value,
                    and_(
                        q.state == QuestionState.ANSWERING.value,
                        q.claimed_at < at - CLAIM_TIMEOUT,
                    ),
                ),
            )
            .values(state=QuestionState.ANSWERING.value, claimed_at=at)
            .returning(q)
        )
        return question_entity(row) if row is not None else None

    async def release(self, message_id: UUID) -> None:
        """The asker refused the answer: the question is open again."""
        q = ChannelQuestionModel
        await self.session.execute(
            update(q)
            .where(q.message_id == message_id, q.state == QuestionState.ANSWERING.value)
            .values(state=QuestionState.OPEN.value, claimed_at=None)
        )

    async def settle(
        self,
        message_id: UUID,
        *,
        state: QuestionState,
        answer: Mapping[str, object] | None,
        by: Principal | None,
    ) -> Question | None:
        """Close a question with what took effect; nothing if already closed.
        Returns it closed now, or ``None``."""
        q = ChannelQuestionModel
        row = await self.session.scalar(
            update(q)
            .where(
                q.message_id == message_id,
                q.state.in_([QuestionState.OPEN.value, QuestionState.ANSWERING.value]),
            )
            .values(
                state=state.value,
                answer=dict(answer) if answer is not None else None,
                answered_by_user_id=by.user_id if by else None,
                answered_by_contact_id=by.contact_id if by else None,
                answered_at=now(),
                claimed_at=None,
            )
            .returning(q)
        )
        return question_entity(row) if row is not None else None

    async def settle_ref(
        self,
        *,
        pod_id: UUID,
        ref: str,
        state: QuestionState,
        answer: Mapping[str, object] | None = None,
        by: Principal | None = None,
    ) -> Question | None:
        found = await self.by_ref(pod_id=pod_id, ref=ref)
        if found is None:
            return None
        return await self.settle(found.message_id, state=state, answer=answer, by=by)

    async def cancel_under(self, *, pod_id: UUID, prefix: str) -> list[Question]:
        """Cancel every open question whose ref starts with ``prefix``."""
        q = ChannelQuestionModel
        rows = await self.session.scalars(
            update(q)
            .where(
                q.pod_id == pod_id,
                q.ref.like(_escape_like(prefix) + "%", escape="\\"),
                q.state.in_([QuestionState.OPEN.value, QuestionState.ANSWERING.value]),
            )
            .values(
                state=QuestionState.CANCELLED.value, answered_at=now(), claimed_at=None
            )
            .returning(q)
        )
        return [question_entity(row) for row in rows]

    async def expire_due(self, *, at: datetime, limit: int) -> list[Question]:
        """Close the questions whose time is up, oldest first."""
        q = ChannelQuestionModel
        due = (
            select(q.message_id)
            .where(q.state == QuestionState.OPEN.value, q.expires_at <= at)
            .order_by(q.expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        rows = await self.session.scalars(
            update(q)
            .where(q.message_id.in_(due))
            .values(state=QuestionState.EXPIRED.value, answered_at=at)
            .returning(q)
        )
        return [question_entity(row) for row in rows]

    # -- reading --------------------------------------------------------------------

    async def under(
        self, *, pod_id: UUID, prefix: str, open_only: bool, limit: int = MAX_LINES
    ) -> list[Question]:
        """Questions whose ref starts with ``prefix``, newest first."""
        q = ChannelQuestionModel
        statement = select(q).where(
            q.pod_id == pod_id, q.ref.like(_escape_like(prefix) + "%", escape="\\")
        )
        if open_only:
            statement = statement.where(
                q.state.in_([QuestionState.OPEN.value, QuestionState.ANSWERING.value])
            )
        rows = await self.session.scalars(
            statement.order_by(q.created_at.desc()).limit(max(1, min(limit, MAX_LINES)))
        )
        return [question_entity(row) for row in rows]

    async def open_in_batch(self, *, pod_id: UUID, batch_id: str) -> int:
        q = ChannelQuestionModel
        count = await self.session.scalar(
            select(func.count()).where(
                q.pod_id == pod_id,
                q.batch_id == batch_id,
                q.state.in_([QuestionState.OPEN.value, QuestionState.ANSWERING.value]),
            )
        )
        return int(count or 0)

    async def waiting_on(
        self, viewer: Viewer, *, limit: int = MAX_LINES
    ) -> list[Question]:
        """Questions waiting on this member, in channels they may see, newest first."""
        q = ChannelQuestionModel
        rows = await self.session.scalars(
            select(q)
            .join(ChannelModel, ChannelModel.id == q.channel_id)
            .where(
                q.awaits_user_id == viewer.user_id,
                q.pod_id == viewer.pod_id,
                q.state == QuestionState.OPEN.value,
                visible_to(viewer),
            )
            .order_by(q.created_at.desc())
            .limit(max(1, min(limit, MAX_LINES)))
        )
        return [question_entity(row) for row in rows]

    async def waiting_on_contact(self, contact_id: UUID) -> list[Question]:
        q = ChannelQuestionModel
        rows = await self.session.scalars(
            select(q)
            .where(
                q.awaits_contact_id == contact_id,
                q.state == QuestionState.OPEN.value,
            )
            .order_by(q.created_at.desc())
            .limit(MAX_LINES)
        )
        return [question_entity(row) for row in rows]
