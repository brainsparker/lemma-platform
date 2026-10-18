from __future__ import annotations

from uuid import UUID

from ..openapi_client.api.agent_surfaces import (
    agent_source_get,
    agent_source_list,
    agent_source_telegram_link_create,
    agent_source_update,
    agent_source_whatsapp_group_start,
)
from ..openapi_client.models.source_list_response import SourceListResponse
from ..openapi_client.models.source_settings_response import SourceSettingsResponse
from ..openapi_client.models.source_update_request import SourceUpdateRequest
from ..openapi_client.models.telegram_link_request import TelegramLinkRequest
from ..openapi_client.models.telegram_link_response import TelegramLinkResponse
from ..openapi_client.models.whats_app_group_request import WhatsAppGroupRequest
from .base import BoundResource, as_uuid


class PodSources(BoundResource):
    """The outside places a pod's group channels are fed by, from the bot's side.

    A WhatsApp or Telegram group, a Slack or Teams channel, a group DM: each is
    a source of a GROUP channel, read and written in through ``channels``. Here
    is only what the bot keeps about it -- whether people outside the pod are
    answered there, and which member answers for them -- and the two ways to
    bring the bot into a group: ``start_whatsapp_group`` (a business number
    creates groups, it cannot join one; it comes back ``pending`` until
    WhatsApp confirms it) and ``telegram_link`` (a one-use, hour-long link that
    adds the bot to a group the caller picks).
    """

    def list(self, surface_name: str) -> SourceListResponse:
        """Every group one bot is in, with each one's settings, newest first."""
        return self._call(
            agent_source_list, self._pod_uuid(), surface_name=surface_name
        )

    def get(self, source_id: str | UUID) -> SourceSettingsResponse:
        """A group source's settings: who answers its outsiders, and from what."""
        return self._call(agent_source_get, self._pod_uuid(), as_uuid(source_id))

    def update(
        self,
        source_id: str | UUID,
        *,
        answers_outsiders: bool | None = None,
        take_over: bool = False,
    ) -> SourceSettingsResponse:
        """Switch outsiders on or off in one group, or take it over."""
        return self._call(
            agent_source_update,
            self._pod_uuid(),
            as_uuid(source_id),
            body={"answers_outsiders": answers_outsiders, "take_over": take_over},
            body_model=SourceUpdateRequest,
        )

    def start_whatsapp_group(
        self, surface_name: str, title: str, *, answers_outsiders: bool = True
    ) -> SourceSettingsResponse:
        """Start a WhatsApp group with the pod's bot in it: a new group channel."""
        return self._call(
            agent_source_whatsapp_group_start,
            self._pod_uuid(),
            body={
                "surface_name": surface_name,
                "title": title,
                "answers_outsiders": answers_outsiders,
            },
            body_model=WhatsAppGroupRequest,
        )

    def telegram_link(self, surface_name: str) -> TelegramLinkResponse:
        """A one-use, hour-long Telegram link that adds the bot to a group."""
        return self._call(
            agent_source_telegram_link_create,
            self._pod_uuid(),
            body={"surface_name": surface_name},
            body_model=TelegramLinkRequest,
        )
