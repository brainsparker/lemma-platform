"""Questions a line puts to one person: who answers them, and what answering does.

A question is asked by another module -- an agent's paused ``ask_user`` or
``request_approval``, a workflow's form, a message an agent sent and wants an
answer to -- and posted as a line with a ``ref`` in its question: a prefix
naming that module's kind of question, then whatever it needs to find it again
(``tool:<conversation>:<call>``, ``wf:<run>:<node>``). Channels cannot import
those modules, so each registers what answering its kind does; the answer
endpoint calls it, and only then closes the line.

A question answered somewhere else -- in the conversation, by tapping a
platform's button, by a reply typed on the platform -- is closed here by the
module that saw it, through ``close_question``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import facts
from app.modules.channels.contracts.facts import ChannelQuestionClosed
from app.modules.channels.domain.entities import (
    ChannelMessage,
    InteractionState,
    Principal,
)
from app.modules.channels.infrastructure.lines import LineRepository


class AnswerRefused(Exception):
    """The asking side would not take this answer; the reason is the person's."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class Answering:
    """One member answering one open question, as its asking side sees it."""

    pod_id: UUID
    message: ChannelMessage
    ref: str
    answer: Mapping[str, object]
    user_id: UUID


AnswerHandler = Callable[[Answering], Awaitable[None]]

_HANDLERS: dict[
    str, AnswerHandler
] = {}  # memory: bounded one per question kind, set at import


def ref_kind(ref: str) -> str:
    """The prefix naming which module's question a ref is."""
    return ref.split(":", 1)[0]


def answers_for(kind: str) -> Callable[[AnswerHandler], AnswerHandler]:
    """Register what answering ``kind`` questions does. Once per kind."""

    def register(handler: AnswerHandler) -> AnswerHandler:
        existing = _HANDLERS.get(kind)
        if existing is not None and existing is not handler:
            raise ValueError(f"Questions of kind {kind!r} already have an answerer")
        _HANDLERS[kind] = handler
        return handler

    return register


def answerer(ref: str) -> AnswerHandler | None:
    return _HANDLERS.get(ref_kind(ref))


async def close_question(
    uow: SqlAlchemyUnitOfWork,
    *,
    ref: str,
    state: InteractionState,
    answer: Mapping[str, object] | None = None,
    by: Principal | None = None,
) -> list[ChannelMessage]:
    """Stop the open lines asking ``ref`` from waiting, and say so.

    Nothing when none is open: it was closed already, or never posted.
    """
    closed = await LineRepository(uow.session).close_question(
        ref=ref, state=state, answer=answer, by=by
    )
    for line in closed:
        await announce_closed(uow, line)
    return closed


async def announce_closed(uow: SqlAlchemyUnitOfWork, line: ChannelMessage) -> None:
    interaction = line.interaction
    if interaction is None or line.pod_id is None:
        return
    await facts.emit(
        uow,
        ChannelQuestionClosed(
            pod_id=line.pod_id,
            channel_id=line.channel_id,
            message_id=line.id,
            ref=_ref_of(line),
            state=interaction.state,
            awaited_user_id=interaction.awaits.user_id if interaction.awaits else None,
        ),
    )


async def close_questions_under(
    uow: SqlAlchemyUnitOfWork, *, prefix: str, state: InteractionState
) -> list[ChannelMessage]:
    """Stop every open line whose ``ref`` starts with ``prefix`` from waiting."""
    closed = await LineRepository(uow.session).cancel_questions_under(
        prefix, state=state
    )
    for line in closed:
        await announce_closed(uow, line)
    return closed


async def open_questions_in(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> list[ChannelMessage]:
    """The lines a conversation asked that still wait for an answer."""
    return await LineRepository(uow.session).open_questions_of(
        conversation_id=conversation_id
    )


async def question_for(uow: SqlAlchemyUnitOfWork, ref: str) -> ChannelMessage | None:
    """The newest line asking ``ref``, open or not."""
    return await LineRepository(uow.session).question(ref)


def _ref_of(line: ChannelMessage) -> str | None:
    if line.interaction is None:
        return None
    ref = line.interaction.question.get("ref")
    return ref if isinstance(ref, str) else None


__all__ = [
    "AnswerHandler",
    "announce_closed",
    "AnswerRefused",
    "Answering",
    "answerer",
    "answers_for",
    "close_question",
    "close_questions_under",
    "open_questions_in",
    "question_for",
    "ref_kind",
]
