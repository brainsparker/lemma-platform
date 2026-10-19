"""A member telling another member something: a line in their own channel.

A member cannot write in another member's own channel, so this is how a page
in Lemma -- a doc comment that names somebody, say -- tells them: a line from
the pod's agent, said on the writer's behalf, in the recipient's channel. It
is pushed to them like any line of theirs (``services.channel_deliveries``).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.modules.agent_surfaces.composition import build_direct_messages
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.services.direct_messages import DirectMessage

router = APIRouter(prefix="/pods/{pod_id}/notices", tags=["Agent Surfaces"])


class NoticeRequest(BaseModel):
    to: str = Field(
        min_length=1,
        max_length=320,
        description="The member to tell: a pod member id, user id, or email address.",
    )
    text: str = Field(min_length=1, max_length=4000)


class NoticeResponse(BaseModel):
    message_id: UUID
    channel_id: UUID


@router.post(
    "",
    operation_id="agent.notice.send",
    response_model=NoticeResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_action(Permissions.CONVERSATION_WRITE)],
)
async def send_notice(
    pod_id: UUID, body: NoticeRequest, user: CurrentUser, uow: UoWDep
) -> NoticeResponse:
    """Tell another member of the pod something, in their own channel."""
    messages = build_direct_messages(uow)
    recipient = await messages.membership.resolve_pod_recipient(
        pod_id=pod_id, reference=body.to
    )
    if recipient is None:
        # 404, not 422: from the caller's side, nobody in this pod and somebody
        # outside it are the same fact.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="No pod member matches that."
        )
    try:
        line = await messages.send(
            DirectMessage(
                pod_id=pod_id,
                recipient_user_id=recipient,
                text=body.text,
                actor_user_id=user.id,
            )
        )
    except AgentSurfaceValidationError as refused:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=refused.message
        ) from refused
    await uow.commit()
    return NoticeResponse(message_id=line.id, channel_id=line.channel_id)
