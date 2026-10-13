"""Durable user-approval reconciliation.

Resolving an approval is two steps with very different costs: recording the
user's decision is a single row write, while acting on it can run an approved
command for minutes or hand a decision to a remote Agent Host. Callers that sit
behind a deadline — an HTTP request, a platform webhook — must only ever pay for
the first. This module owns the pieces that let them do that: the deterministic
job identity, the queueing, and the work the queued job performs.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import get_streaq_job_queue
from app.modules.agent.domain.agent_host_permissions import (
    AgentHostPermissionRequest,
    agent_host_permission_request,
)
from app.modules.agent.domain.pausing_tools import parked_suspension_of
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
    to_json_value,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.tool_errors import safe_error_text
from app.core.log.log import get_logger

logger = get_logger(__name__)

RECONCILE_APPROVAL_JOB = "reconcile_agent_approval"


def approval_reconcile_job_id(conversation_id: UUID, approval_id: str) -> str:
    """Return the deterministic worker job id for one approval decision."""
    return f"agent-approval:{conversation_id}:{approval_id}"


async def queue_approval_reconciliation(
    *,
    conversation_id: UUID,
    approval_id: str,
    user_id: UUID,
    pod_id: UUID,
) -> None:
    """Hand one already-recorded decision to the worker.

    The deterministic job id makes a double-click (or a retry after a crashed
    worker) re-enqueue the same job rather than stack a second one, which is
    safe because reconciliation is itself idempotent.
    """
    await get_streaq_job_queue().enqueue(
        RECONCILE_APPROVAL_JOB,
        context={
            "conversation_id": str(conversation_id),
            "approval_id": approval_id,
            "user_id": str(user_id),
            "pod_id": str(pod_id),
        },
        _job_id=approval_reconcile_job_id(conversation_id, approval_id),
    )


def should_defer_approved_tool(
    *,
    defer_reconciliation: bool,
    kind: str,
    tool_args: JsonObject,
    decision: AgentRunApprovalDecision,
    has_tool_return: bool,
) -> bool:
    """Whether this decision's follow-up work belongs in a worker job.

    Only an approved ``request_approval`` runs something slow: commands can
    legitimately take minutes, and keeping that inside the request made a
    browser's 30-second timeout cancel reconciliation after the decision had
    already committed. ``ask_user``, denials, already-executed approvals, and
    Agent Host permissions (a queued command plus a wake-up, measured in
    milliseconds) stay inline, since deferring them would only add latency to
    someone who is waiting.
    """
    return (
        defer_reconciliation
        and kind == "request_approval"
        and decision != AgentRunApprovalDecision.DENY
        and not has_tool_return
        and agent_host_permission_request(tool_args) is None
    )


async def enqueue_agent_host_permission(
    uow: SqlAlchemyUnitOfWork,
    *,
    request: AgentHostPermissionRequest,
    agent_run_id: UUID,
    decision: AgentRunApprovalDecision,
) -> UUID | None:
    """Queue the decision for the host, in the caller's transaction.

    Returns the host to wake once that transaction commits, or ``None`` when
    the run already ended -- the host's own request timeout has taken over and
    there is nothing left to answer.

    In the caller's transaction, beside the tool return that records the
    decision, so the two commit or roll back together. Queued after the commit
    instead, a failure there left a transcript reading "Approved" over a
    request the host never heard about and auto-denied half an hour later. It
    costs one INSERT and a short lock on the run's lease row.

    The command carries a TTL sized to the host's own permission window rather
    than the default five minutes, because delivery is a push away and can
    still be missed if the machine never comes back; nothing here waits for
    the host to apply it, and the wording the agent sees must not claim so.
    """
    # Lazy: the dispatch repository pulls in the Agent Host infrastructure,
    # which imports back through the harnesses.
    from app.modules.agent.infrastructure.agent_host.dispatch_repository import (
        AgentHostDispatchRepository,
    )

    command = await AgentHostDispatchRepository(uow).enqueue_permission_decision(
        run_id=agent_run_id,
        request_id=request.request_id,
        option_id=request.option_for(decision),
    )
    return command.host_id if command is not None else None


async def record_session_approvals(
    *,
    conversation_id: UUID,
    agent_id: UUID | None,
    pod_id: UUID,
    tool_args: JsonObject,
    user_id: UUID,
) -> None:
    """Persist APPROVE_FOR_SESSION as per-permission session approvals, plus an
    exact-match approval for the wrapped call itself.

    Keyed to (conversation, workload actor, permission) — the same key the
    authorizer checks (for `permission_ids`) or `request_approval` itself checks
    before pausing again (for the exact-command key). Structured actions
    (pod/table/folder/... delete) carry `permission_ids` copied from the denied
    tool result, unlocking the whole action TYPE for the rest of the
    conversation. Tools with no structured permission — exec_command,
    execute_python, anything not gated by the authorizer — have no category to
    unlock, so they get only the exact-command key: approving one call lets the
    agent repeat that LITERAL call again without re-prompting, but a different
    command still re-prompts (see session_approvals.exact_command_permission_id
    for why anything looser, e.g. a prefix match, would be a shell-injection
    vector).
    """
    from app.core.authorization.session_approvals import (
        exact_command_permission_id,
        record_session_approval,
    )
    from app.modules.agent.tools.authority import conversation_workload_actor_id

    workload_actor_id = conversation_workload_actor_id(agent_id=agent_id, pod_id=pod_id)

    inner_tool_name = tool_args.get("tool_name")
    if isinstance(inner_tool_name, str) and inner_tool_name:
        inner_args = tool_args.get("args")
        await record_session_approval(
            session_id=str(conversation_id),
            workload_actor_id=workload_actor_id,
            permission_id=exact_command_permission_id(
                inner_tool_name,
                inner_args if isinstance(inner_args, dict) else {},
            ),
            resolved_by_user_id=user_id,
        )

    permission_ids = tool_args.get("permission_ids")
    if not isinstance(permission_ids, list):
        return
    for permission_id in permission_ids:
        if not isinstance(permission_id, str) or not permission_id:
            continue
        await record_session_approval(
            session_id=str(conversation_id),
            workload_actor_id=workload_actor_id,
            permission_id=permission_id,
            resolved_by_user_id=user_id,
        )


async def agent_host_permission_tool_return(
    *,
    uow: SqlAlchemyUnitOfWork,
    request: AgentHostPermissionRequest,
    agent_run_id: UUID,
    decision: AgentRunApprovalDecision,
    response: JsonObject,
) -> JsonObject:
    """Hand the decision to the host and describe it to the agent.

    Shaped as a ``request_approval`` return like every other approval, so the
    conversation transcript reads the same however the pause arose. The host
    command is written in the caller's transaction (see
    ``enqueue_agent_host_permission``); only the wake-up waits for the commit,
    because a Redis publish has no business inside an open write transaction
    and losing one costs at most the link's floor tick.

    ``executed`` stays False, unlike the ordinary request_approval path. It means
    "the wrapped tool ran", and on this path Lemma runs nothing: it queues a
    decision for a machine that will collect it, and the ACP agent then
    proceeds, or does not, on its own.
    """
    # Lazy: the tool models import the tool registry, which imports back here.
    from app.modules.agent.tools.user_interaction.models import RequestApprovalResponse

    host_id = await enqueue_agent_host_permission(
        uow, request=request, agent_run_id=agent_run_id, decision=decision
    )
    if host_id is not None:
        uow.after_commit(lambda: _wake_host(host_id))
    # Read only once the enqueue has had the lease lock: a turn being ended for
    # its parked window commits its mark under that same lock.
    suspended = host_id is None and await _turn_ended_while_parked(uow, agent_run_id)

    content = RequestApprovalResponse(
        success=True,
        message=_permission_decision_message(
            request,
            decision=decision,
            delivered=host_id is not None,
            suspended=suspended,
        ),
        decision=decision,
        executed=False,
        response=response,
    )
    return content.model_dump(mode="json")


async def _wake_host(host_id: UUID) -> None:
    # Lazy for the same reason as the repository above.
    from app.modules.agent.infrastructure.agent_host.channels import poke_host

    await poke_host(host_id)


async def _turn_ended_while_parked(
    uow: SqlAlchemyUnitOfWork, agent_run_id: UUID
) -> bool:
    """Whether Lemma ended this run's turn because the request sat unanswered."""
    # Lazy for the same reason as the dispatch repository above.
    from app.modules.agent.infrastructure.repositories import ConversationRepository

    run = await ConversationRepository(uow).get_agent_run(agent_run_id)
    return run is not None and parked_suspension_of(run.metadata) is not None


def _permission_decision_message(
    request: AgentHostPermissionRequest,
    *,
    decision: AgentRunApprovalDecision,
    delivered: bool,
    suspended: bool = False,
) -> str:
    """What the agent reads about a decision on its permission request."""
    if suspended:
        # Read by a resumed run, in a new turn. The ACP request it answers
        # was dropped with the turn that asked it, and nothing carries an
        # approval into the next one: the agent asks again, and is answered
        # again, if it does the thing at all.
        if decision == AgentRunApprovalDecision.DENY:
            return (
                "Denied. Your earlier turn was paused while this waited for the "
                "person, so the request lapsed and nothing ran. Do not do it."
            )
        return (
            "Approved. Your earlier turn was paused while this waited for the "
            "person, so the request lapsed and nothing ran. If it is still "
            "needed, do it again now; the person will be asked to allow it once "
            "more."
        )
    if not delivered:
        return (
            "The local agent's run ended before this decision reached it, so "
            "nothing was sent; Lemma has not run anything."
        )
    if request.refuses_a_one_time_approval(decision):
        return (
            "Not approved. The local agent offered only to allow this for the "
            "rest of its session, which a one-time approval does not grant, so "
            "it was told not to use the tool. Approve it for the session to let "
            "it run."
        )
    if decision != AgentRunApprovalDecision.DENY:
        return (
            "Approved. The decision is queued for the local agent, which "
            "will pick it up and decide what to do; Lemma has not run anything."
        )
    return (
        "Denied. The decision is queued for the local agent, "
        "which was told not to use the tool."
    )


async def execute_approved_tool_as_user(
    *,
    uow: SqlAlchemyUnitOfWork,
    deps: BaseAgentContext,
    tool_name: str,
    args: dict[str, object],
    approval_id: str | None = None,
) -> dict[str, object]:
    """Run an approved tool outside the database transaction; never raise."""
    # Lazy to avoid importing the tool registry back through ConversationService.
    from app.modules.agent.tools.approval.executor import ApprovalExecutor

    try:
        # Approved commands may run for minutes. Release the checked-out DB
        # connection before crossing that external boundary; otherwise a command
        # timeout can leave us appending its tool return through a connection the
        # server closed while it sat idle. The repository session checks out a
        # fresh one for the append/resume transaction that follows.
        await uow.commit()
        executor = ApprovalExecutor(SessionUnitOfWorkFactory(async_session_maker))
        result = await executor.execute_as_user(
            deps=deps,
            tool_name=tool_name,
            args=args,
            approval_id=approval_id,
        )
        value = to_json_value(result)
        if isinstance(value, dict) and value.get("success") is False:
            error = value.get("error") or value.get("message")
            return {
                "ok": False,
                "error": str(error or f"{tool_name} did not complete"),
            }
        return {"ok": True, "value": value}
    except Exception as exc:  # noqa: BLE001 - reported to the model, not fatal
        # The model only ever sees the redacted line below, so without this an
        # approved action that failed left nothing anyone could debug from.
        logger.error(
            "agent.approval.approved_tool_execution.failed",
            tool_name=tool_name,
            approval_id=approval_id,
            exc_info=True,
        )
        # Redacted, not `str(exc)`: this string is persisted as the approval's
        # tool return, replayed into the model on every later turn and read by
        # anyone with the conversation. An approved tool is the class of call
        # most likely to be an authenticated HTTP request, and
        # `httpx.HTTPStatusError` stringifies with the full URL -- a signed
        # storage link or a connector callback carrying a token in its query.
        return {"ok": False, "error": safe_error_text(exc)}
