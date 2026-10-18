"""An agent's questions, as lines that wait on the person they ask.

When a run pauses on ``ask_user`` or ``request_approval``, the question is a
line -- in the channel the conversation lives in, or, for a conversation in no
channel (a chat in Lemma, a scheduled task), in the member's own channel -- so
it is in their inbox as something waiting on them, wherever they are. It is
answered there (``answer_tool_question``), in the conversation, on a
platform's card or in a reply typed on the platform; whichever way, the next
time a run of the conversation ends its lines are brought in line with what is
still undecided (``sync_pause_lines``): a question decided elsewhere closes
with that decision, one nobody can answer any more is cancelled.

The pause itself stays the agent module's: these lines show it and answer it,
and never decide anything on their own.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import effective_agent_id
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.contracts import channel_conversations
from app.modules.agent.contracts import conversations_for_surfaces as conversations
from app.modules.agent.contracts.agents import agent_name_for_id
from app.modules.agent.contracts.conversations_for_surfaces import PendingInteraction
from app.modules.agent.contracts.pauses import (
    answer_pause,
    pause_decisions,
    undecided_pauses_in,
)
from app.modules.channels.contracts import (
    ChannelMessage,
    InteractionKind,
    InteractionState,
    NewQuestion,
    Principal,
    Sender,
    direct_channel,
    post,
)
from app.modules.channels.contracts.questions import (
    AnswerRefused,
    Answering,
    answers_for,
    close_question,
    open_questions_in,
    question_for,
)

#: What a question's ``ref`` starts with: an agent's paused tool call.
TOOL_REF = "tool"
_ASK = "ask_user"
_APPROVAL = "request_approval"
_DECISIONS = frozenset({"APPROVE_ONCE", "APPROVE_FOR_SESSION", "DENY"})


def tool_ref(conversation_id: UUID, tool_call_id: str) -> str:
    return f"{TOOL_REF}:{conversation_id}:{tool_call_id}"


def _parse_ref(ref: str) -> tuple[UUID, str] | None:
    kind, _, rest = ref.partition(":")
    conversation, _, call = rest.partition(":")
    if kind != TOOL_REF or not call:
        return None
    try:
        return UUID(conversation), call
    except ValueError:
        return None


def question_of(pause: PendingInteraction) -> tuple[str, dict[str, object]]:
    """The line's words, and what it shows to be answered."""
    args = pause.tool_args
    if pause.kind == _APPROVAL:
        title = str(args.get("title") or "May I go ahead?")
        reason = args.get("reason")
        return (
            f"{title}\n{reason}" if reason else title,
            {
                "tool": _APPROVAL,
                "prompt": title,
                "reason": reason,
                "action": args.get("tool_name"),
                "choices": ["APPROVE_ONCE", "APPROVE_FOR_SESSION", "DENY"],
            },
        )
    raw = args.get("questions")
    questions = [q for q in raw if isinstance(q, dict)] if isinstance(raw, list) else []
    first = str(questions[0].get("question") or "") if questions else ""
    more = f" (+{len(questions) - 1} more)" if len(questions) > 1 else ""
    return (first + more) or "A question for you", {
        "tool": _ASK,
        "prompt": first,
        "questions": questions,
    }


async def sync_pause_lines(uow: SqlAlchemyUnitOfWork, conversation_id: UUID) -> None:
    """Post a line for each question the conversation newly waits on, and close
    the lines of the ones no longer waiting."""
    conversation = await conversations.surface_conversation(uow, conversation_id)
    if conversation is None or conversation.audience.contact_id is not None:
        # A contact's run never pauses; nothing of theirs to show.
        return
    pending = await undecided_pauses_in(uow, conversation_id)
    waiting = {tool_ref(conversation_id, pause.tool_call_id) for pause in pending}
    for pause in pending:
        ref = tool_ref(conversation_id, pause.tool_call_id)
        if await question_for(uow, ref) is None:
            await _ask(uow, conversation, pause, ref)
    stale = [
        line
        for line in await open_questions_in(uow, conversation_id)
        if _ref_of(line) is not None and _ref_of(line) not in waiting
    ]
    await _close(uow, conversation_id, stale)


def _ref_of(line: ChannelMessage) -> str | None:
    if line.interaction is None:
        return None
    ref = line.interaction.question.get("ref")
    return ref if isinstance(ref, str) and ref.startswith(f"{TOOL_REF}:") else None


async def _ask(
    uow: SqlAlchemyUnitOfWork,
    conversation: conversations.SurfaceConversation,
    pause: PendingInteraction,
    ref: str,
) -> None:
    member = Principal.member(conversation.user_id)
    channel_id = await channel_conversations.channel_of_conversation(
        uow, conversation.id
    )
    if channel_id is None:
        channel_id = (
            await direct_channel(
                uow, pod_id=conversation.pod_id, user_id=conversation.user_id
            )
        ).id
    agent_id = effective_agent_id(conversation.agent_id, pod_id=conversation.pod_id)
    text, shown = question_of(pause)
    await post(
        uow,
        channel_id=channel_id,
        sender=Sender.agent(
            agent_id,
            on_behalf_of=member,
            display_name=await agent_name_for_id(uow.session, agent_id),
        ),
        text=text,
        conversation_id=conversation.id,
        agent_run_id=pause.agent_run_id,
        question=NewQuestion(
            kind=InteractionKind.APPROVAL
            if pause.kind == _APPROVAL
            else InteractionKind.ASK,
            question={"ref": ref, "conversation_id": str(conversation.id), **shown},
            awaits=member,
        ),
    )


async def _close(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, stale: list[ChannelMessage]
) -> None:
    calls = {
        ref: parsed[1]
        for line in stale
        if (ref := _ref_of(line)) and (parsed := _parse_ref(ref))
    }
    decisions = await pause_decisions(
        uow, conversation_id=conversation_id, tool_call_ids=list(calls.values())
    )
    for ref, call in calls.items():
        decided = decisions.get(call)
        if decided is None:
            await close_question(uow, ref=ref, state=InteractionState.CANCELLED)
            continue
        await close_question(
            uow,
            ref=ref,
            state=InteractionState.ANSWERED,
            answer={"decision": decided.decision, **(decided.response or {})},
            by=(
                Principal.member(decided.resolved_by_user_id)
                if decided.resolved_by_user_id
                else None
            ),
        )


def _decision(
    tool: object, answer: Mapping[str, object], questions: object
) -> tuple[str, dict[str, object]]:
    """The decision and response the pause takes, from an answer given on a line."""
    if tool == _APPROVAL:
        decision = str(answer.get("decision") or "APPROVE_ONCE").upper()
        if decision not in _DECISIONS:
            raise AnswerRefused("Approve or deny it", status_code=422)
        note = answer.get("note")
        return decision, ({"note": note} if isinstance(note, str) and note else {})
    answers = answer.get("answers")
    if isinstance(answers, dict) and answers:
        return "APPROVE_ONCE", {"answers": dict(answers)}
    said = answer.get("choice") or answer.get("text")
    header = (
        questions[0].get("header")
        if isinstance(questions, list) and questions and isinstance(questions[0], dict)
        else None
    )
    if not isinstance(said, str) or not said.strip() or not header:
        raise AnswerRefused("Pick an answer, or write one", status_code=422)
    return "APPROVE_ONCE", {"answers": {str(header): said.strip()}}


@answers_for(TOOL_REF)
async def answer_tool_question(answering: Answering) -> None:
    """A question an agent asked, answered on its line: the paused run takes it
    as if it had been answered in the conversation."""
    parsed = _parse_ref(answering.ref)
    interaction = answering.message.interaction
    if parsed is None or interaction is None:
        raise AnswerRefused("This question cannot be answered here", status_code=404)
    conversation_id, tool_call_id = parsed
    decision, response = _decision(
        interaction.question.get("tool"),
        answering.answer,
        interaction.question.get("questions"),
    )
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        context = await create_authorization_data_service(uow).build_user_context(
            user_id=answering.user_id, pod_id=answering.pod_id
        )
        token = set_current_context(context)
        try:
            await answer_pause(
                uow,
                conversation_id=conversation_id,
                tool_call_id=tool_call_id,
                user_id=answering.user_id,
                pod_id=answering.pod_id,
                decision=decision,
                response=response,
            )
            await uow.commit()
        except DomainError as refused:
            raise AnswerRefused(
                refused.message, status_code=refused.status_code or 409
            ) from refused
        finally:
            reset_current_context(token)
