"""``surface.member_post``: a member wrote in Lemma for an outside place.

Subscribed to ``ChannelMessagePosted`` for lines the posting API marks for
delivery. A JOB: it calls a platform. A retry finds the line already sent --
it carries the platform's delivery -- or sends it again, which a platform
outage between the send and its record can make happen at most once more.
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
    SURFACE_MEMBER_POST,
    member_post_key,
)
from app.modules.agent_surfaces.services.member_posts import deliver_member_line
from app.modules.channels.contracts.facts import ChannelMessagePosted


class MemberPostPayload(BaseModel):
    """The fields of ``ChannelMessagePosted`` this needs; the rest are ignored."""

    message_id: UUID


def make_member_post(uow_factory: UnitOfWorkFactory | None = None):
    async def member_post(ctx: JobContext, payload: MemberPostPayload) -> None:
        del ctx
        await deliver_member_line(
            uow_factory or SessionUnitOfWorkFactory(async_session_maker),
            message_id=payload.message_id,
        )

    return member_post


def register_member_post_work(
    registry: WorkRegistry, *, uow_factory: UnitOfWorkFactory | None = None
) -> None:
    registry.handler(
        kind=SURFACE_MEMBER_POST,
        cls=WorkClass.DELIVERY,
        mode=WorkMode.JOB,
        payload=MemberPostPayload,
    )(make_member_post(uow_factory))


register_member_post_work(default_registry)
facts.subscribe(
    ChannelMessagePosted,
    kind=SURFACE_MEMBER_POST,
    cls=WorkClass.DELIVERY,
    when=lambda fact: fact.deliver,
    dedup=lambda fact: member_post_key(fact.message_id),
)
