from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
)
from app.modules.agent_surfaces.domain.setup_actions import SurfaceSetupAction
from app.modules.agent_surfaces.domain.setup_guides import SurfacePlatformSetupGuide
from app.modules.agent_surfaces.domain.surface_representation import (
    AgentSurfaceResponse,
    SurfaceBehaviorConfigInput,
)


class AvailableSurfaceChannelResponse(BaseModel):
    """A channel/group the surface bot can be configured to respond in."""

    id: str
    name: str | None = None
    is_member: bool | None = None


class AvailableSurfaceChannelsResponse(BaseModel):
    channels: list[AvailableSurfaceChannelResponse] = Field(default_factory=list)


class SurfaceUpdateRequest(BaseModel):
    """Body for `PATCH /pods/{pod_id}/surfaces/{surface_name}`.

    Partial update (merge semantics): only fields present in the request are
    applied. The surface's ``platform`` and ``name`` are immutable — delete and
    recreate to change either.
    """

    default_agent_name: str | None = None
    account_id: UUID | None = None
    credential_mode: SurfaceCredentialMode | None = None
    config: SurfaceBehaviorConfigInput = Field(
        default_factory=SurfaceBehaviorConfigInput
    )
    is_enabled: bool | None = None

    model_config = ConfigDict(extra="forbid")


class TelegramManagedBotSetupRequest(BaseModel):
    name: str | None = Field(
        default=None,
        description="Pod-unique surface name. Defaults to telegram.",
    )
    default_agent_name: str | None = None
    config: SurfaceBehaviorConfigInput = Field(
        default_factory=SurfaceBehaviorConfigInput
    )
    is_enabled: bool = True

    model_config = ConfigDict(extra="forbid")


class TelegramManagedBotSetupResponse(BaseModel):
    setup_id: str
    status: str
    launch_url: str
    manager_bot_username: str
    expires_at: str
    account_id: UUID | None = None
    surface_id: UUID | None = None
    bot_username: str | None = None
    bot_launch_url: str | None = None
    error: str | None = None


class AgentSurfaceListResponse(BaseModel):
    items: list[AgentSurfaceResponse]
    limit: int
    next_page_token: str | None = None


class SurfaceSendRequest(BaseModel):
    """Send a proactive message to a pod member on this surface."""

    user_id: UUID = Field(..., description="Target pod member (Lemma user id).")
    message: str = Field(..., min_length=1, description="Message text to deliver.")


class SurfaceSendResponse(BaseModel):
    sent: bool


class SurfaceAdminConsentInfo(BaseModel):
    """Admin-consent state for surfaces that require an OAuth grant (Teams)."""

    required: bool = False
    granted: bool = False
    consent_url: str | None = None


class SurfaceSetupResponse(BaseModel):
    """Everything a caller needs to finish setting up a surface, in one read.

    Merges the former setup-status, admin-consent, and platform-checklist
    endpoints. Works both before a surface exists (`exists=False`, guide only)
    and after.

    ``ready`` is True when the user has nothing left to do (system credentials,
    or an already-granted consent). ``actions`` is populated *only* when the
    user must act — e.g. point their own Slack/Teams/WhatsApp app at Lemma —
    so the UI can show a clean "Ready" state otherwise.
    """

    platform: SurfacePlatform
    exists: bool
    status: AgentSurfaceStatus
    ready: bool = False
    webhook_url: str | None = None
    admin_consent: SurfaceAdminConsentInfo | None = None
    actions: list[SurfaceSetupAction] = Field(default_factory=list)
    guide: SurfacePlatformSetupGuide


class NotificationResponse(BaseModel):
    """One notification, shaped for the inbox that renders it.

    Carries enough to draw the row *and* decide what its action button does,
    without a second request: ``awaiting_response`` says whether to draw one at
    all, and ``responds_through_action`` says whether it opens a text box or the
    real form described by ``action``.
    """

    id: UUID
    pod_id: UUID
    title: str
    body: str
    origin_kind: NotificationOriginKind
    origin_id: UUID | None = None
    origin_conversation_id: UUID | None = None

    # Who asked. Both, because "the pod's bot" is not an answer a person can act
    # on and the human behind a run is the one they will reply to.
    actor_user_id: UUID | None = None
    actor_agent_id: UUID | None = None

    status: NotificationStatus
    delivery_status: NotificationDeliveryStatus
    expects_response: bool
    awaiting_response: bool
    responds_through_action: bool
    action: dict[str, Any] | None = None

    delivery_platform: str | None = None
    delivery_conversation_id: UUID | None = None
    # Why nobody could be reached, in words a person can act on ("they have not
    # messaged the bot yet"). Present only when delivery did not succeed.
    undeliverable_reason: str | None = None

    response_summary: str | None = None
    response_data: dict[str, Any] | None = None

    created_at: datetime
    expires_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    responded_at: datetime | None = None

    @classmethod
    def from_entity(cls, entity: NotificationEntity) -> "NotificationResponse":
        return cls(
            id=entity.id,
            pod_id=entity.pod_id,
            title=entity.title,
            body=entity.body,
            origin_kind=entity.origin_kind,
            origin_id=entity.origin_id,
            origin_conversation_id=entity.origin_conversation_id,
            actor_user_id=entity.actor_user_id,
            actor_agent_id=entity.actor_agent_id,
            status=entity.status,
            delivery_status=entity.delivery_status,
            expects_response=entity.expects_response,
            awaiting_response=entity.awaiting_response,
            responds_through_action=entity.responds_through_action,
            action=entity.action,
            delivery_platform=entity.delivery_platform,
            delivery_conversation_id=entity.delivery_conversation_id,
            undeliverable_reason=entity.delivery_error,
            response_summary=entity.response_summary,
            response_data=entity.response_data,
            created_at=entity.created_at,
            expires_at=entity.expires_at,
            delivered_at=entity.delivered_at,
            read_at=entity.read_at,
            responded_at=entity.responded_at,
        )
        # NB: ``background_instruction`` is deliberately absent. It is addressed
        # to the agent handling the reply and carries the asker's private
        # framing; showing it to the recipient would leak how they are being
        # managed.


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse] = Field(default_factory=list)
    limit: int
    next_page_token: str | None = None


class NotificationUnreadCountResponse(BaseModel):
    # Keyed on read_at, not status: a badge that only clears when you *answer*
    # is a badge people learn to ignore.
    unread: int


class NotificationRespondRequest(BaseModel):
    summary: str = Field(min_length=1, description="The answer, in the person's words.")
    data: dict[str, Any] | None = Field(
        default=None, description="Optional structured payload alongside the answer."
    )

    model_config = ConfigDict(extra="forbid")


class NotifyMemberRequest(BaseModel):
    """Send a notification to one pod member."""

    recipient: str = Field(
        description="Pod member id, user id, or email address of the recipient."
    )
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)
    background_instruction: str | None = Field(
        default=None,
        description=(
            "Never shown to the recipient. Tells the agent that handles their "
            "reply what to do with it."
        ),
    )
    expects_response: bool = True
    expires_in_seconds: int | None = Field(default=None, gt=0)

    model_config = ConfigDict(extra="forbid")
