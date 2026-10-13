"""Sending a finished run's one reply -- its answer, or why there is none.

Split out of :class:`SurfaceAgentRunProgressObserver` by use case: the observer
watches a run go by, and this is what it says once the run has stopped. Both
halves -- the answer and the failure notice -- go through the observer's one
send, and keeping them together is what keeps "nothing sent twice" one rule.

Free functions over the observer's state rather than another mixin: the
observer is already assembled from several, and each base is one more class a
reader has to hold to know where an attribute comes from.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts import Conversation
from app.modules.agent_surfaces.platforms.delivery import SendOutcome, track_sends
from app.modules.agent_surfaces.services.owed_reply import (
    OwedReply,
    owe_reply,
    owes_a_retry,
)
from app.modules.agent_surfaces.services.pending_envelope import RunFiles

logger = get_logger(__name__)

#: What enqueueing the deferred retry can fail with: the queue is Redis.
_SCHEDULE_ERRORS: tuple[type[BaseException], ...] = (RedisError, OSError)


class ReplyState(Protocol):
    """What of the observer the reply reads and records."""

    uow_factory: UnitOfWorkFactory
    _run_files: RunFiles
    _final_delivered: bool
    _final_send_failed: bool
    _final_answer_text: str | None
    _buffered_text: str | None
    _answer_was_all_reasoning: bool
    _run_errored: bool
    _run_error_text: str | None
    _error_delivered: bool

    async def _deliver_run_error(self, conversation: Conversation) -> None: ...

    async def _send_agent_message(
        self,
        *,
        conversation_id: UUID,
        message: str,
        metadata: dict[str, object] | None = None,
        carries_files: bool = True,
    ) -> bool: ...


async def deliver_final_answer(state: ReplyState, conversation: Conversation) -> None:
    """Deliver the single final answer once the run has finished.

    Every surface, the same way: the buffered answer goes out when the run
    stops. Email used to be the exception, deferring to a reply tool the agent
    had to remember to call -- and this path, which ran whenever the agent
    forgot, was never a lesser implementation of it. It threads correctly,
    renders markdown to HTML and keeps the thread's subject, which is the whole
    of what the tool did.

    Nothing is sent if the run errored (the error is delivered instead) or if
    there is no usable text.
    """
    if state._final_delivered:
        return
    if state._run_errored:
        state._final_delivered = True
        await state._deliver_run_error(conversation)
        return
    message = (state._final_answer_text or state._buffered_text or "").strip()
    if not message and state._answer_was_all_reasoning:
        # An answer existed and sanitizing removed all of it, so the run
        # "succeeded" with nothing to show. Saying nothing is the one outcome
        # the person cannot act on — they cannot tell it from the agent never
        # having seen the message, and will ask again into the same silence.
        # Say the turn produced nothing instead.
        message = (
            "I thought that through but never wrote the answer. "
            "Ask me again and I'll give it another go."
        )
    if not message:
        state._final_delivered = True
        return
    # Marked delivered only once it was, not before the send: latching first
    # meant a send that failed still counted as the answer having gone out, and
    # nothing that read the flag afterwards could tell the difference.
    with track_sends() as outcome:
        try:
            delivered = await state._send_agent_message(
                conversation_id=conversation.id,
                message=message,
            )
        except Exception:
            logger.warning(
                "agent_surfaces.progress_observer.final_answer_not_delivered.degraded",
                conversation_id=str(conversation.id),
                exc_info=True,
            )
            delivered = False
    if delivered:
        state._final_delivered = True
        return
    state._final_send_failed = True
    if owes_a_retry(outcome):
        await _owe(state, conversation, message, outcome)
        return
    # The send path says why -- no surface link, a platform refusal -- so this
    # only records that the answer did not go out.
    logger.debug(
        "agent_surfaces.progress_observer.final_answer_unsent.diagnostic",
        conversation_id=str(conversation.id),
    )


async def _owe(
    state: ReplyState, conversation: Conversation, message: str, outcome: SendOutcome
) -> None:
    """Hand an answer the platform would not take yet to a deferred retry.

    The run's held files stay held for it: ``on_run_finished`` keeps them when
    the send failed, and the retry is the reply that carries them.
    """
    try:
        await owe_reply(
            OwedReply(
                conversation_id=conversation.id,
                agent_run_id=state._run_files.agent_run_id,
                message=message,
            ),
            retry_after=outcome.retry_after,
            uow_factory=state.uow_factory,
        )
    except _SCHEDULE_ERRORS:
        logger.warning(
            "agent_surfaces.progress_observer.owed_reply_not_scheduled.degraded",
            conversation_id=str(conversation.id),
            exc_info=True,
        )


async def deliver_run_error(state: ReplyState, conversation: Conversation) -> None:
    """Say that the run failed, on every surface including email.

    Email used to be skipped here, so a failed run on an email surface said
    nothing at all -- the person's message simply vanished, and nothing
    distinguished that from never having been read. Now the run stopping is the
    run stopping, whatever stopped it, and the one reply carries the failure.
    """
    if state._error_delivered:
        return
    try:
        delivered = await state._send_agent_message(
            conversation_id=conversation.id,
            message=(
                state._run_error_text
                or "I couldn’t finish that request. You can try it again."
            ),
            metadata={"retry_action": True},
            carries_files=False,
        )
    except Exception:
        logger.warning(
            "agent_surfaces.progress_observer.run_error_not_delivered.degraded",
            conversation_id=str(conversation.id),
            exc_info=True,
        )
        return
    if delivered:
        state._error_delivered = True
