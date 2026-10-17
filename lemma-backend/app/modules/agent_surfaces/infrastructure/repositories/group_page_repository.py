"""The reads behind a pod's list of groups, each asked for every group at once.

The list shows, for every group, what is waiting on the person reading; who
has spoken there is the group's channel's to say. Asked one group at a time that was a set of queries per
group -- a few thousand for a busy pod -- so each read here takes all of the
pod's groups and answers in one statement.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.groups import (
    CONTACT_LINK_USER_PREFIX,
    SurfaceGroup,
)
from app.modules.agent_surfaces.domain.notification import NotificationStatus
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceConversationLinkModel,
    NotificationModel,
)

#: Ceilings on what one list read returns. A pod lists at most a few hundred
#: groups, each a chat a person can read.
_MAX_CONVERSATIONS = 2000


class GroupPageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def outsider_conversations(
        self, groups: Sequence[SurfaceGroup]
    ) -> dict[UUID, list[UUID]]:
        """Each group's conversations with its people outside the pod."""
        keyed = {
            (group.surface_id, group.external_channel_id): group.id
            for group in groups
            if group.external_channel_id
        }
        if not keyed:
            return {}
        link = AgentSurfaceConversationLinkModel
        rows = (
            await self.session.execute(
                select(link.surface_id, link.external_channel_id, link.conversation_id)
                .where(
                    tuple_(link.surface_id, link.external_channel_id).in_(list(keyed)),
                    link.external_user_id.startswith(
                        CONTACT_LINK_USER_PREFIX, autoescape=True
                    ),
                )
                .limit(_MAX_CONVERSATIONS)
            )
        ).all()
        found: dict[UUID, list[UUID]] = defaultdict(list)
        for surface_id, channel_id, conversation_id in rows:
            found[keyed[(surface_id, channel_id)]].append(conversation_id)
        return dict(found)

    async def open_asks_by_conversation(
        self, *, recipient_user_id: UUID, conversation_ids: Sequence[UUID]
    ) -> dict[UUID, int]:
        """How many questions each conversation has open with this person."""
        if not conversation_ids:
            return {}
        notification = NotificationModel
        rows = (
            await self.session.execute(
                select(notification.origin_conversation_id, func.count())
                .where(
                    notification.recipient_user_id == recipient_user_id,
                    notification.origin_conversation_id.in_(list(conversation_ids)),
                    notification.expects_response.is_(True),
                    notification.status == NotificationStatus.OPEN.value,
                )
                .group_by(notification.origin_conversation_id)
                .limit(len(conversation_ids))
            )
        ).all()
        return dict(rows)
