"""The screen's toolbar, and what a viewer reports about how the stream went.

Two routes beside the view socket. The toolbar's commands are a closed set --
back, forward, reload, go to an address, list tabs, bring one forward, close
one -- validated here and again by the relay, which carries them out through
the same browser the agent drives. The beacon is a viewer saying, once a
session ends, what it actually got: the transport, the frame rate, the
latency, and why it fell back to VNC if it did. That is the only place those
numbers exist; nothing on the server sees a frame arrive.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.api.dependencies import CurrentUser
from app.core.log.log import get_logger
from app.modules.workspace.services.browser_relay_client import (
    BrowserRelayUnavailable,
)
from app.modules.workspace.services.browser_view_service import BrowserViewService
from sandbox_runtime.errors import SandboxCapabilityUnsupported

logger = get_logger(__name__)

router = APIRouter(prefix="/workspace/browser", tags=["Workspace Apps"])


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NavigateCommand(_Closed):
    command: Literal["navigate"]
    url: str = Field(min_length=1, max_length=2048)


class HistoryCommand(_Closed):
    command: Literal["back", "forward", "reload"]


class TabsCommand(_Closed):
    command: Literal["tabs"]


class TabCommand(_Closed):
    command: Literal["activate", "close"]
    target: str = Field(pattern=r"^[A-Fa-f0-9]{8,64}$")


BrowserCommand = Annotated[
    NavigateCommand | HistoryCommand | TabsCommand | TabCommand,
    Field(discriminator="command"),
]


class BrowserCommandRequest(_Closed):
    action: BrowserCommand


class BrowserTab(BaseModel):
    id: str
    title: str
    url: str


class BrowserCommandResponse(BaseModel):
    tabs: list[BrowserTab] = Field(default_factory=list)


def browser_view_service() -> BrowserViewService:
    return BrowserViewService()


@router.post(
    "/commands",
    response_model=BrowserCommandResponse,
    operation_id="workspace.browser.command",
    summary="Steer the workspace browser from the screen's toolbar",
)
async def browser_command(
    user: CurrentUser,
    request: BrowserCommandRequest,
    service: Annotated[BrowserViewService, Depends(browser_view_service)],
) -> BrowserCommandResponse:
    """One toolbar command; the open tabs as they are afterwards.

    409 with a sentence when the browser cannot do it right now -- it is not
    running, the tab has closed -- because the toolbar shows it, and 422 for
    an address that is not a web address.
    """
    try:
        answer = await service.browser_command(
            user.id, request.action.model_dump(exclude_none=True)
        )
    except SandboxCapabilityUnsupported as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except BrowserRelayUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "workspace.browser_view.command_failed.degraded",
            command=request.action.command,
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="the browser did not answer"
        ) from exc
    finally:
        await service.close()
    return BrowserCommandResponse.model_validate(answer)


class StreamBeacon(_Closed):
    """What one viewing session got, from the viewer's side.

    Bounded everywhere: it comes from a browser and ends up in a log line.
    """

    transport: Literal["vnc", "webrtc"]
    #: Why WebRTC was not used, when it was meant to be.
    fallback: (
        Literal[
            "ice-timeout",
            "ice-failed",
            "no-streamer",
            "no-turn",
            "unsupported",
            "error",
        ]
        | None
    ) = None
    seconds: float = Field(ge=0, le=86_400)
    first_frame_ms: float | None = Field(default=None, ge=0, le=600_000)
    fps: float | None = Field(default=None, ge=0, le=240)
    latency_ms: float | None = Field(default=None, ge=0, le=60_000)
    kbps: float | None = Field(default=None, ge=0, le=1_000_000)
    codec: Literal["h264", "vp8"] | None = None
    relay: Literal["udp", "tcp", "tls"] | None = None
    density: int | None = Field(default=None, ge=1, le=2)


@router.post(
    "/beacon",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="workspace.browser.beacon",
    summary="Report how a live screen session went",
)
async def stream_beacon(user: CurrentUser, beacon: StreamBeacon) -> None:
    logger.info(
        "workspace.browser_view.stream_session.observed",
        user_id=str(user.id),
        transport=beacon.transport,
        fallback=beacon.fallback,
        seconds=beacon.seconds,
        first_frame_ms=beacon.first_frame_ms,
        fps=beacon.fps,
        latency_ms=beacon.latency_ms,
        kbps=beacon.kbps,
        codec=beacon.codec,
        relay=beacon.relay,
        density=beacon.density,
    )


__all__ = ["router"]
