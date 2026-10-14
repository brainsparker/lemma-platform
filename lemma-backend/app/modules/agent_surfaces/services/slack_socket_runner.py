"""Holding a Slack app's Socket Mode connection, for a runtime with no public URL.

Split from the receiver coordinator, which leases and supervises receivers of
every platform; this is the Slack one, beside its Telegram and Resend peers.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.core.config import reveal_secret
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.credentials import SlackCredentials
from app.modules.agent_surfaces.services.native_receiver_base import (
    NativeReceiverCandidate,
    _publish_native_receiver_event,
)

if TYPE_CHECKING:
    from slack_sdk.socket_mode.async_client import AsyncBaseSocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest

logger = get_logger(__name__)


# The Socket Mode envelope types whose payload is the same JSON Slack POSTs to
# the HTTP webhook: an Events API event, and an interaction (a button press, a
# modal submission -- the HTTP route unwraps its form-encoded `payload=` field to
# exactly this). Slash commands are the third envelope type and are not here on
# purpose: the HTTP route has no reading of one either, so publishing it would
# only queue a payload nothing can parse.
_PUBLISHED_ENVELOPE_TYPES = frozenset({"events_api", "interactive"})


class SlackSocketReceiverRunner:
    def __init__(self, candidate: NativeReceiverCandidate) -> None:
        self._candidate = candidate

    async def _handle_envelope(
        self, socket_client: AsyncBaseSocketModeClient, req: SocketModeRequest
    ) -> None:
        """Publish what this envelope carries, and only then acknowledge it.

        Two things were wrong with the listener this replaces. It answered Slack
        first and published second, so a Redis failure between the two lost the
        event for good -- Slack had been told it arrived. And it dropped every
        envelope that was not an Events API event, so on Socket Mode a button or
        a modal never did anything at all.

        Unacknowledged is the right state for an envelope that could not be
        published: Slack redelivers it, which is the only retry there is.
        """
        from slack_sdk.socket_mode.response import SocketModeResponse

        if req.type in _PUBLISHED_ENVELOPE_TYPES:
            published = False
            try:
                await _publish_native_receiver_event(
                    source="slack",
                    payload=req.payload,
                    receiver_key=self._candidate.key,
                    surface_ids=self._candidate.surface_ids,
                )
                published = True
            finally:
                # The failure propagates -- Slack's client logs it and leaves
                # the envelope unacknowledged -- and this is the record that
                # carries the reason and says which envelope it was.
                if not published:
                    logger.warning(
                        "agent_surfaces.event_receiver_service.slack_socket_publish_failed.failed",
                        envelope_type=req.type,
                        # LOG014 reads "not inside an `except`" as "no exception
                        # to attach"; this runs while one unwinds through a
                        # `finally`, where it is still the current one.
                        exc_info=True,  # noqa: LOG014
                    )
        else:
            logger.debug(
                "agent_surfaces.event_receiver_service.slack_socket_envelope_ignored.observed",
                envelope_type=req.type,
            )
        await socket_client.send_socket_mode_response(
            SocketModeResponse(envelope_id=req.envelope_id)
        )

    async def run(self) -> None:
        slack = SlackCredentials.of(self._candidate.credentials)
        # Revealed into the Socket Mode client, which holds the connection.
        app_token = (reveal_secret(slack.app_token) or "").strip()
        if not app_token:
            logger.debug(
                "agent_surfaces.event_receiver_service.slack_native_receiver_missing_app.diagnostic"
            )
            return

        from slack_sdk.socket_mode.aiohttp import SocketModeClient
        from slack_sdk.web.async_client import AsyncWebClient

        client = SocketModeClient(
            app_token=app_token,
            web_client=AsyncWebClient(token=reveal_secret(slack.token) or None),
        )

        client.socket_mode_request_listeners.append(self._handle_envelope)
        try:
            await client.connect()
            while True:
                await asyncio.sleep(3600)
        finally:
            await client.close()
