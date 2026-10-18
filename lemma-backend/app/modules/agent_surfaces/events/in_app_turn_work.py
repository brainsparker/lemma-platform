"""``surface.in_app_turn``: a member wrote in Lemma, and an agent answers.

Subscribed to ``ChannelMessagePosted`` for a member's line that sets an agent
answering: one written in their own channel, or one naming an agent. A JOB,
not FAST: answering authorizes the member, which commits, before it opens the
conversation and starts the run. Starting a turn twice for one line is
harmless -- the second finds the first's conversation, and a turn arriving
while one runs is handed to that run -- so a retry may repeat it.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_IN_APP_TURN,
    in_app_turn_key,
)
from app.modules.agent_surfaces.services.in_app_turns import answer_in_app
from app.modules.channels.contracts import ChannelKind, SenderKind
from app.modules.channels.contracts.facts import ChannelMessagePosted


class InAppTurnPayload(BaseModel):
    """The fields of ``ChannelMessagePosted`` this needs; the rest are ignored."""

    message_id: UUID
    mentioned_agent_ids: tuple[UUID, ...] = ()


def sets_an_agent_answering(fact: ChannelMessagePosted) -> bool:
    """A member's line an agent answers: named in it, or -- in the member's own
    channel, written in Lemma -- the pod's agent."""
    if fact.sender_kind is not SenderKind.MEMBER or fact.members_only:
        return False
    if fact.mentioned_agent_ids:
        return True
    return fact.channel_kind is ChannelKind.DIRECT and fact.source_id is None


def make_in_app_turn(uow_factory: UnitOfWorkFactory | None = None):
    async def in_app_turn(ctx: JobContext, payload: InAppTurnPayload) -> None:
        del ctx
        await answer_in_app(
            uow_factory or SessionUnitOfWorkFactory(async_session_maker),
            message_id=payload.message_id,
            mentioned=payload.mentioned_agent_ids,
        )

    return in_app_turn


def register_in_app_turn_work(
    registry: WorkRegistry, *, uow_factory: UnitOfWorkFactory | None = None
) -> None:
    registry.handler(
        kind=SURFACE_IN_APP_TURN,
        cls=WorkClass.INTERACTIVE,
        mode=WorkMode.JOB,
        payload=InAppTurnPayload,
    )(make_in_app_turn(uow_factory))


register_in_app_turn_work(default_registry)
facts.subscribe(
    ChannelMessagePosted,
    kind=SURFACE_IN_APP_TURN,
    cls=WorkClass.INTERACTIVE,
    when=sets_an_agent_answering,
    dedup=lambda fact: in_app_turn_key(fact.message_id),
)
