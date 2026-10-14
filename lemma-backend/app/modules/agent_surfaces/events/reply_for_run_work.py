"""``surface.reply_for_run``: a finished run's answer, owed to its surface thread.

Subscribed to the agent module's ``AgentRunFinished``, so the item is written
in the transaction that finished the run: once a run has an outcome, the reply
it owes its thread is on record, and the window in which a worker could die
between the run ending and the send -- with nothing left to resend from -- is
gone.

The row is the *backstop*. The run's own observer normally sends the answer
within a second, through the same row (``claim_run_reply``), because it alone
can close a live Slack stream with the answer in it. So the row is written due
``OBSERVER_GRACE`` from now: if the observer took it, the pump finds it SENT;
if the observer's worker died, the pump sends the run's stored answer.

FAST: one run row, one link row, one insert. No network in this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.contracts.run_replies import run_reply_material
from app.modules.agent_surfaces.domain.deliveries import (
    OBSERVER_GRACE,
    ConversationTarget,
    DeliveryContent,
)
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_REPLY_FOR_RUN,
    run_reply_key,
)
from app.modules.agent_surfaces.services.surface_deliveries import (
    conversation_destination,
    run_reply_idempotency_key,
    stage_delivery,
)

_REPLYING_STATUSES = frozenset({"COMPLETED", "FAILED"})


class RunReplyPayload(BaseModel):
    """The fields of ``AgentRunFinished`` this needs; the rest are ignored."""

    run_id: UUID
    conversation_id: UUID
    pod_id: UUID
    status: str


def owes_a_reply(fact: AgentRunFinished) -> bool:
    """A run that ended with an answer or a failure; not one that paused.

    Whether the conversation is on a surface at all is read in the handler,
    from the link -- one cheap item per finished run rather than a predicate
    trusting a flag the finisher had to remember to set.
    """
    return fact.status in _REPLYING_STATUSES and fact.conversation_status != "WAITING"


async def stage_run_reply(ctx: FastContext, payload: RunReplyPayload) -> None:
    material = await run_reply_material(ctx.uow, payload.run_id)
    if material is None or material.private:
        return
    failed = payload.status == "FAILED"
    if not failed and not material.answer:
        return
    destination, surface_id = await conversation_destination(
        ctx.uow, payload.conversation_id
    )
    if surface_id is None:
        return
    await stage_delivery(
        ctx.uow,
        idempotency_key=run_reply_idempotency_key(payload.run_id),
        target=ConversationTarget(conversation_id=payload.conversation_id),
        content=DeliveryContent(
            text="",
            purpose="failure" if failed else "answer",
            metadata={"retry_action": True} if failed else {},
            attach_files_of_run=None if failed else payload.run_id,
        ),
        destination_key=destination,
        pod_id=payload.pod_id,
        surface_id=surface_id,
        conversation_id=payload.conversation_id,
        agent_run_id=payload.run_id,
        not_before=datetime.now(UTC) + OBSERVER_GRACE,
        work=ctx.work,
    )


def register_reply_for_run_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACE_REPLY_FOR_RUN,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.FAST,
        payload=RunReplyPayload,
    )(stage_run_reply)


register_reply_for_run_work(default_registry)
facts.subscribe(
    AgentRunFinished,
    kind=SURFACE_REPLY_FOR_RUN,
    cls=WorkClass.DELIVERY,
    when=owes_a_reply,
    dedup=lambda fact: run_reply_key(fact.run_id),
)
