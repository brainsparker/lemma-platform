"""Sending a finished run's one reply -- its answer, or why there is none.

Split out of :class:`SurfaceAgentRunProgressObserver` by use case: the observer
watches a run go by, and this is what it says once the run has stopped. Both
halves -- the answer and the failure notice -- go through the observer's one
send, and keeping them together is what keeps "nothing sent twice" one rule.

The reply is a ``surface_deliveries`` row keyed by the run
(``run-reply:<run>``). The finished run writes that row as a backstop, due a
little later; the observer *claims* it before it sends (``claim_run_reply``),
so whichever of the two gets there first sends and the other finds it taken.
A send the platform refused for now is handed to the destination pump behind
the ``Retry-After`` it asked for, and one whose outcome was lost (the worker
died mid-send) is UNKNOWN and never repeated.

Free functions over the observer's state rather than another mixin: the
observer is already assembled from several, and each base is one more class a
reader has to hold to know where an attribute comes from.
"""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts import Conversation
from app.modules.agent_surfaces.domain.deliveries import DeliveryContent
from app.modules.agent_surfaces.platforms.delivery import SendOutcome, track_sends
from app.modules.agent_surfaces.services.pending_envelope import RunFiles
from app.modules.agent_surfaces.services.surface_deliveries import (
    SendResult,
    claim_run_reply,
    settle_claimed_send,
)

logger = get_logger(__name__)

#: The claim is somebody else's: the pump is sending it, or already has.
TAKEN: Literal["taken"] = "taken"
#: No run to key it by (or no database to record it in): send unrecorded.
UNKEYED: Literal["unkeyed"] = "unkeyed"
type ReplyClaim = UUID | Literal["taken", "unkeyed"]


class ReplyLedger(Protocol):
    """Where a run's one reply is claimed before it is sent, and settled after."""

    async def claim(
        self,
        *,
        run_id: UUID,
        conversation_id: UUID,
        pod_id: UUID | None,
        content: DeliveryContent,
    ) -> UUID | None:
        """The row to send, or None when somebody else has it."""
        ...

    async def settle(self, row_id: UUID, result: SendResult) -> bool:
        """Record the send's outcome; True when a retry is scheduled."""
        ...


class DeliveryReplyLedger:
    """The ledger in ``surface_deliveries``: the row the finished run wrote."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self.uow_factory = uow_factory

    async def claim(
        self,
        *,
        run_id: UUID,
        conversation_id: UUID,
        pod_id: UUID | None,
        content: DeliveryContent,
    ) -> UUID | None:
        return await claim_run_reply(
            self.uow_factory,
            run_id=run_id,
            conversation_id=conversation_id,
            pod_id=pod_id,
            content=content,
        )

    async def settle(self, row_id: UUID, result: SendResult) -> bool:
        async with self.uow_factory() as uow:
            return await settle_claimed_send(uow, row_id, result) is not None


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
    _reply_claim: ReplyClaim | None
    reply_ledger: ReplyLedger

    async def _deliver_run_error(self, conversation: Conversation) -> None: ...

    async def _send_agent_message(
        self,
        *,
        conversation_id: UUID,
        message: str,
        metadata: dict[str, object] | None = None,
        carries_files: bool = True,
    ) -> bool: ...


def final_answer_text(state: ReplyState) -> str:
    """The answer the run wrote, or the sentence that stands in for a lost one."""
    message = (state._final_answer_text or state._buffered_text or "").strip()
    if not message and state._answer_was_all_reasoning:
        # An answer existed and sanitizing removed all of it, so the run
        # "succeeded" with nothing to show. Saying nothing is the one outcome
        # the person cannot act on -- they cannot tell it from the agent never
        # having seen the message, and will ask again into the same silence.
        message = (
            "I thought that through but never wrote the answer. "
            "Ask me again and I'll give it another go."
        )
    return message


async def claim_reply(
    state: ReplyState,
    conversation: Conversation,
    *,
    text: str,
    purpose: str,
    metadata: dict[str, object] | None = None,
    carries_files: bool = True,
) -> ReplyClaim:
    """Take this run's reply to send it here, once, and remember the answer.

    Asked again it returns what it returned the first time: the stream close
    and the plain send that may follow it are one reply.
    """
    if state._reply_claim is not None:
        return state._reply_claim
    run_id = state._run_files.agent_run_id
    if run_id is None:
        state._reply_claim = UNKEYED
        return UNKEYED
    try:
        claimed = await state.reply_ledger.claim(
            run_id=run_id,
            conversation_id=conversation.id,
            pod_id=getattr(conversation, "pod_id", None),
            content=DeliveryContent(
                text=text,
                purpose=purpose,
                metadata=dict(metadata or {}),
                attach_files_of_run=run_id if carries_files else None,
            ),
        )
    except SQLAlchemyError:
        # No record means no backstop either, so the reply goes out unrecorded
        # rather than not at all: the database being down is not a reason for
        # the person to hear nothing.
        logger.warning(
            "agent_surfaces.progress_reply.claim_failed.degraded",
            conversation_id=str(conversation.id),
            exc_info=True,
        )
        state._reply_claim = UNKEYED
        return UNKEYED
    state._reply_claim = claimed if claimed is not None else TAKEN
    if claimed is None:
        logger.info(
            "agent_surfaces.progress_reply.reply_taken.observed",
            conversation_id=str(conversation.id),
        )
    return state._reply_claim


async def settle_reply(
    state: ReplyState,
    conversation: Conversation,
    *,
    delivered: bool,
    outcome: SendOutcome | None = None,
    error: str | None = None,
) -> bool:
    """Record how the claimed send went. True when a retry is scheduled."""
    claim = state._reply_claim
    if not isinstance(claim, UUID):
        return False
    result = SendResult(
        delivered=delivered,
        transient=bool(outcome and outcome.gave_up_transient),
        partial=bool(outcome and outcome.partial),
        retry_after=outcome.retry_after if outcome else None,
        error=error,
    )
    try:
        return await state.reply_ledger.settle(claim, result)
    except SQLAlchemyError:
        # Left SENDING, so the pump reads the outcome as lost and never sends
        # it again: a delivered answer is not repeated for want of this write.
        logger.warning(
            "agent_surfaces.progress_reply.settle_failed.degraded",
            conversation_id=str(conversation.id),
            exc_info=True,
        )
        return False


async def deliver_final_answer(state: ReplyState, conversation: Conversation) -> None:
    """Deliver the single final answer once the run has finished.

    Every surface, the same way: the buffered answer goes out when the run
    stops. Nothing is sent if the run errored (the error is delivered instead)
    or if there is no usable text.
    """
    if state._final_delivered:
        return
    if state._run_errored:
        state._final_delivered = True
        await state._deliver_run_error(conversation)
        return
    message = final_answer_text(state)
    if not message:
        state._final_delivered = True
        return
    claim = await claim_reply(state, conversation, text=message, purpose="answer")
    if claim == TAKEN:
        # Somebody else is sending it, with this run's files: they are theirs
        # to release, not the observer's.
        state._final_delivered = True
        state._final_send_failed = True
        return
    # Marked delivered only once it was, not before the send: latching first
    # meant a send that failed still counted as the answer having gone out.
    error: str | None = None
    with track_sends() as outcome:
        try:
            delivered = await state._send_agent_message(
                conversation_id=conversation.id,
                message=message,
            )
        except Exception as exc:
            logger.warning(
                "agent_surfaces.progress_observer.final_answer_not_delivered.degraded",
                conversation_id=str(conversation.id),
                exc_info=True,
            )
            delivered = False
            error = f"{type(exc).__name__}: {exc}"
    retrying = await settle_reply(
        state, conversation, delivered=delivered, outcome=outcome, error=error
    )
    if delivered:
        state._final_delivered = True
        return
    # The run's held files stay held: for the pump's retry when there is one,
    # and otherwise for whoever recovers this reply before they expire.
    state._final_send_failed = True
    logger.debug(
        "agent_surfaces.progress_observer.final_answer_unsent.diagnostic",
        conversation_id=str(conversation.id),
        retrying=retrying,
    )


async def deliver_run_error(state: ReplyState, conversation: Conversation) -> None:
    """Say that the run failed, on every surface including email."""
    if state._error_delivered:
        return
    message = (
        state._run_error_text or "I couldn’t finish that request. You can try it again."
    )
    claim = await claim_reply(
        state,
        conversation,
        text=message,
        purpose="failure",
        metadata={"retry_action": True},
        carries_files=False,
    )
    if claim == TAKEN:
        state._error_delivered = True
        return
    error: str | None = None
    with track_sends() as outcome:
        try:
            delivered = await state._send_agent_message(
                conversation_id=conversation.id,
                message=message,
                metadata={"retry_action": True},
                carries_files=False,
            )
        except Exception as exc:
            logger.warning(
                "agent_surfaces.progress_observer.run_error_not_delivered.degraded",
                conversation_id=str(conversation.id),
                exc_info=True,
            )
            delivered = False
            error = f"{type(exc).__name__}: {exc}"
    await settle_reply(
        state, conversation, delivered=delivered, outcome=outcome, error=error
    )
    if delivered:
        state._error_delivered = True
