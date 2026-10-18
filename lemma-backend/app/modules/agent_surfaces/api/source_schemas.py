"""What the sources API reads and writes: a group source's settings.

A group's timeline, people and unread lines are its channel's
(``/pods/{pod_id}/channels``); this is only what the bot's side of it keeps.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.agent_surfaces.api import group_public as public_api
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    GROUP_SUBJECT_MAX_CHARS,
)
from app.modules.agent_surfaces.services.source_settings import SourceState
from app.modules.channels.contracts.people import PersonRef, SenderKind


class SourceSettingsResponse(BaseModel):
    id: UUID = Field(description="The source's id, as the channel lists it.")
    channel_id: UUID
    surface_name: str
    platform: str
    title: str | None = None
    invite_link: str | None = None
    pending: bool = Field(
        default=False,
        description="Asked of the platform and not yet confirmed (WhatsApp).",
    )
    shared_externally: bool = Field(
        default=False,
        description="A Slack channel shared with another company.",
    )
    owner: PersonRef | None = Field(
        default=None, description="The member who answers for its outsiders."
    )
    answers_outsiders: bool = Field(
        description="This group's own switch for people outside the pod."
    )
    welcomes_outsiders: bool = Field(
        description=(
            "People outside the pod are answered here today: the group's switch "
            "is on, a member of the pod answers for them, and the bot's own "
            "switch is on."
        )
    )
    bot_answers_outsiders: bool = Field(
        default=True,
        description=(
            "The bot's own switch, over every group it is in. Off, nobody outside "
            "the pod is answered in any of them."
        ),
    )
    can_manage: bool = Field(
        default=False,
        description=(
            "The reader may switch outsiders here or take it over: they answer "
            "for it, nobody in the pod does, or they are an admin of the pod."
        ),
    )
    public: public_api.GroupPublicResponse | None = Field(
        default=None, description="What people outside the pod can be answered from."
    )
    updated_at: datetime


class SourceListResponse(BaseModel):
    items: list[SourceSettingsResponse]


class WhatsAppGroupRequest(BaseModel):
    surface_name: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=GROUP_SUBJECT_MAX_CHARS)
    answers_outsiders: bool = True


class SourceUpdateRequest(BaseModel):
    answers_outsiders: bool | None = Field(
        default=None,
        description="Answer people outside the pod here, or stop.",
    )
    take_over: bool = Field(
        default=False,
        description="Answer for its outsiders yourself from now on.",
    )


class TelegramLinkRequest(BaseModel):
    surface_name: str = Field(min_length=1)


class TelegramLinkResponse(BaseModel):
    url: str = Field(description="Opens Telegram's own choose-a-group screen.")
    expires_at: datetime


def settings_response(
    state: SourceState,
    *,
    can_manage: bool,
    public: public_api.GroupPublicResponse | None = None,
) -> SourceSettingsResponse:
    group = state.group
    # A member who has left the pod answers for nobody, and reads as nobody.
    owner = (
        PersonRef(kind=SenderKind.MEMBER, id=group.owner_user_id, name=state.owner_name)
        if group.owner_user_id is not None and state.owner_in_pod
        else None
    )
    return SourceSettingsResponse(
        id=group.id,
        channel_id=group.channel_id,
        surface_name=state.surface_name,
        platform=group.platform,
        title=group.title,
        invite_link=group.invite_link,
        pending=group.is_pending,
        shared_externally=group.shared_externally,
        owner=owner,
        answers_outsiders=group.answers_outsiders,
        welcomes_outsiders=state.answers_outsiders_now,
        bot_answers_outsiders=state.bot_answers_outsiders,
        can_manage=can_manage,
        public=public,
        updated_at=group.updated_at,
    )
