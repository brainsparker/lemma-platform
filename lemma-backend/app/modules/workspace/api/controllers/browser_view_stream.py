"""The browser view socket's WebRTC branch, and the names viewers go by.

Its own module so the socket controller stays one route long. Setting up WebRTC
has a part that can refuse -- TURN credentials, which a deployment may not
have -- and a part that runs once the socket is accepted; the controller calls
the first before `accept()` so a refusal arrives as a close code the pane
reads, and the second after.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import WebSocket

from app.modules.workspace.services.browser_stream_turn import IceServer
from app.modules.workspace.services.browser_view_rtc import (
    CLOSE_USE_VNC,
    RtcUnavailable,
    RtcViewer,
    ice_for,
    serve,
)
from app.modules.workspace.services.browser_view_service import (
    MODE_CONTROL,
    ViewUpstream,
)

#: What a pane may call itself. It reaches the relay inside a viewer name and
#: ends up in its logs, so it is held to a shape that cannot forge a line.
VIEWER_PATTERN = r"^[A-Za-z0-9-]{1,40}$"


def viewer_name(user_id: UUID, pane: str | None) -> str | None:
    """`<user>:<pane>`, the name the relay knows this viewer by.

    Prefixed with the user here, never by the client, so one person's pane
    can never be named as another's -- the resize lease is held by name.
    """
    return f"{user_id}:{pane}" if pane else None


@dataclass(frozen=True)
class PreparedRtc:
    target: ViewUpstream
    viewer: RtcViewer
    machine_ice: list[IceServer]
    browser_ice: list[IceServer]


async def prepare_rtc(
    target: ViewUpstream, *, user_id: UUID, mode: str, viewer: str | None
) -> PreparedRtc:
    """Everything a WebRTC view needs before its socket is accepted.

    Raises `RtcUnavailable` when there is no relay to be had, which the
    caller turns into `CLOSE_USE_VNC`.

    `may_control` is true for everybody who reaches this route today: it is
    the owner's own machine, keyed by their user id. It is a field rather
    than a constant so that a shared machine's view-only viewer is one
    change away, and so the streamer -- not the pane -- is what enforces it.
    """
    rtc_viewer = RtcViewer(
        sub=viewer_name(user_id, viewer) or str(user_id),
        provider_id=target.provider_id,
        mode=MODE_CONTROL if mode == MODE_CONTROL else "view",
        may_control=True,
    )
    machine_ice, browser_ice = await ice_for(rtc_viewer)
    return PreparedRtc(target, rtc_viewer, machine_ice, browser_ice)


async def serve_rtc_view(websocket: WebSocket, prepared: PreparedRtc) -> int | None:
    """Carry the signalling; the relay's 44xx close code, if it gave one."""
    return await serve(
        websocket,
        upstream_url=prepared.target.url,
        headers=prepared.target.headers,
        viewer=prepared.viewer,
        machine_ice=prepared.machine_ice,
        browser_ice=prepared.browser_ice,
    )


__all__ = [
    "CLOSE_USE_VNC",
    "VIEWER_PATTERN",
    "PreparedRtc",
    "RtcUnavailable",
    "prepare_rtc",
    "serve_rtc_view",
    "viewer_name",
]
