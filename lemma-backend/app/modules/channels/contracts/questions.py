"""Questions a line puts to one person: what other modules ask, answer and close.

An asker -- an agent's paused tool call (``tool:``), a workflow's form
(``wf:``), a question one member's agent put to another (``msg:``) -- posts a
line with a ``NewQuestion`` (``channels.contracts.post``), registers what
taking an answer does (``answers_for``), and closes questions answered or
ended elsewhere (``close_ref``, ``cancel_under``). ``ChannelQuestionClosed``
announces every close.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import Question
from app.modules.channels.infrastructure.questions import QuestionRepository
from app.modules.channels.services.questions import (
    APPROVAL_DECISIONS,
    AnswerHandler,
    AnswerRefused,
    Answering,
    answer,
    answers_for,
    cancel_under,
    close,
    close_ref,
    expire_overdue,
    registered_askers,
)

__all__ = [
    "APPROVAL_DECISIONS",
    "AnswerHandler",
    "AnswerRefused",
    "Answering",
    "answer",
    "answers_for",
    "cancel_under",
    "close",
    "close_ref",
    "expire_overdue",
    "open_in_batch",
    "question_by_ref",
    "question_of",
    "questions_under",
    "registered_askers",
    "waiting_on",
]


async def question_by_ref(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, ref: str
) -> Question | None:
    return await QuestionRepository(uow.session).by_ref(pod_id=pod_id, ref=ref)


async def question_of(uow: SqlAlchemyUnitOfWork, message_id: UUID) -> Question | None:
    return await QuestionRepository(uow.session).of_message(message_id)


async def questions_under(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    prefix: str,
    open_only: bool = False,
    limit: int = 100,
) -> list[Question]:
    """Questions whose ref starts with ``prefix``, newest first."""
    return await QuestionRepository(uow.session).under(
        pod_id=pod_id, prefix=prefix, open_only=open_only, limit=limit
    )


async def open_in_batch(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, batch_id: str
) -> int:
    """How many questions sent together are still waiting."""
    return await QuestionRepository(uow.session).open_in_batch(
        pod_id=pod_id, batch_id=batch_id
    )


async def waiting_on(
    uow: SqlAlchemyUnitOfWork, viewer: Viewer, *, limit: int = 100
) -> list[Question]:
    """The questions waiting on this member, in channels they may see."""
    return await QuestionRepository(uow.session).waiting_on(viewer, limit=limit)
