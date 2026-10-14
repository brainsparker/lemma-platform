"""The link queries keyed on a person's private chat rather than its address.

A mixin of ``SurfaceConversationLinkRepository`` rather than a repository of its
own, so callers keep asking the one link repository; split out only to keep
that file inside the line ceiling.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.agent_surfaces.domain.entities import AgentSurfaceConversationLink
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceConversationLinkModel,
)


class DmLinkQueries:
    """Reads and writes keyed on the person rather than the thread address."""

    #: Supplied by the repository this is mixed into.
    session: Session

    async def find_latest_dm_link_for_person(
        self,
        *,
        platform: str,
        external_user_id: str,
        surface_ids: Collection[UUID],
    ) -> AgentSurfaceConversationLink | None:
        """This person's most recent private-chat link on any of these surfaces.

        The exact-thread reads above key on a delivery address -- the surface, the
        channel and the thread id -- and for a private chat the address is not what
        makes it the same conversation. On WhatsApp it embeds the number the
        message arrived on, so a reassigned number or a different serving surface
        reads as a chat nobody has spoken in. This is the person-level answer to
        "is there already a conversation here", and it is asked only after the
        exact one has missed.

        ``surface_ids`` is required, and is what keeps this read on
        ``ix_agent_surface_link_surface_member`` (surface, person, recency):
        without a surface list the same question would scan every link of the
        platform. Ordered by inbound recency for the reason
        ``list_latest_by_surface_and_external_users`` gives.
        """
        person = {"platform": platform, "external_user_id": external_user_id}
        links = await self.latest_dm_links_for_person(**person, surface_ids=surface_ids)
        return links[0] if links else None

    async def latest_dm_links_for_person(
        self,
        *,
        platform: str,
        external_user_id: str,
        surface_ids: Collection[UUID],
        limit: int = 1,
    ) -> list[AgentSurfaceConversationLink]:
        """The above, several deep, for a caller that wants one pod's chat."""
        if not surface_ids:
            return []
        recency = func.coalesce(
            AgentSurfaceConversationLinkModel.last_inbound_at,
            AgentSurfaceConversationLinkModel.updated_at,
        )
        stmt = (
            select(AgentSurfaceConversationLinkModel)
            .where(
                AgentSurfaceConversationLinkModel.platform == platform,
                AgentSurfaceConversationLinkModel.external_user_id == external_user_id,
                AgentSurfaceConversationLinkModel.conversation_kind == "DM",
                AgentSurfaceConversationLinkModel.surface_id.in_(list(surface_ids)),
            )
            .order_by(recency.desc())
            .limit(limit)
        )
        models = (await self.session.execute(stmt)).scalars().all()
        return [model.to_entity() for model in models]

    async def rebind_thread_address(
        self,
        *,
        link: AgentSurfaceConversationLink,
        surface_id: UUID,
        external_channel_id: str | None,
        external_thread_id: str,
    ) -> AgentSurfaceConversationLink | None:
        """Point an existing link at the address its chat is now delivered on.

        The conversation, the person and the agent are untouched: only where the
        chat is reached changes. The caller holds the thread lock for the *new*
        address, which does not stop another message adopting the same link from
        a third address -- so the move compares the address it read, and the
        loser gets None and opens its own conversation instead of stealing the
        link back.
        """
        model = AgentSurfaceConversationLinkModel
        stmt = (
            update(model)
            .where(
                model.id == link.id,
                model.surface_id == link.surface_id,
                model.external_channel_id.is_not_distinct_from(
                    link.external_channel_id
                ),
                model.external_thread_id == link.external_thread_id,
            )
            .values(
                surface_id=surface_id,
                external_channel_id=external_channel_id,
                external_thread_id=external_thread_id,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(model)
            .execution_options(populate_existing=True)
        )
        moved = (await self.session.execute(stmt)).scalar_one_or_none()
        return moved.to_entity() if moved else None
