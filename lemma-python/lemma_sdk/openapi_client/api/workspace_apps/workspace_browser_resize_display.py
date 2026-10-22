from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.display_size_request import DisplaySizeRequest
from ...models.display_size_response import DisplaySizeResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: DisplaySizeRequest,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    params: dict[str, Any] = {}

    json_pod_id: None | str | Unset
    if isinstance(pod_id, Unset):
        json_pod_id = UNSET
    elif isinstance(pod_id, UUID):
        json_pod_id = str(pod_id)
    else:
        json_pod_id = pod_id
    params["pod_id"] = json_pod_id

    json_agent: None | str | Unset
    if isinstance(agent, Unset):
        json_agent = UNSET
    elif isinstance(agent, UUID):
        json_agent = str(agent)
    else:
        json_agent = agent
    params["agent"] = json_agent

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/workspace/browser/display-size",
        "params": params,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DisplaySizeResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = DisplaySizeResponse.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[DisplaySizeResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: DisplaySizeRequest,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> Response[DisplaySizeResponse | ErrorResponse]:
    """Fit the workspace display to the pane showing it

     Resize the sandbox display so the picture matches the pane.

    The alternative, and what this replaces, is one fixed display scaled to
    fit: a 3:2 screen letterboxed into whatever box it lands in, small and
    ringed with dead space. Resizing the display itself means the pixels sent
    are the pixels shown -- and a narrow pane gets a narrow *viewport*, so a
    site serves its mobile layout to somebody signing in on a phone.

    A failure here is not an error for the person: they keep the display they
    had. So an unreachable or sleeping sandbox answers with no size rather
    than a status code the pane would have to special-case.

    Args:
        pod_id (None | Unset | UUID): The pod whose workspace to use. Each pod has its own, with
            its own files and browser. Omitted, your personal workspace.
        agent (None | Unset | UUID): A named agent of `pod_id`, to use that agent's workspace.
            Each agent has its own. Omitted, the pod assistant's.
        body (DisplaySizeRequest): The size the pane wants its picture to be, in CSS pixels.

            Bounded here because these numbers come from a browser window and decide
            how much memory a framebuffer takes. The sandbox clamps again against the
            framebuffer it actually allocated, which is the limit that cannot be
            argued with.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DisplaySizeResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        body=body,
        pod_id=pod_id,
        agent=agent,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: DisplaySizeRequest,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> DisplaySizeResponse | ErrorResponse | None:
    """Fit the workspace display to the pane showing it

     Resize the sandbox display so the picture matches the pane.

    The alternative, and what this replaces, is one fixed display scaled to
    fit: a 3:2 screen letterboxed into whatever box it lands in, small and
    ringed with dead space. Resizing the display itself means the pixels sent
    are the pixels shown -- and a narrow pane gets a narrow *viewport*, so a
    site serves its mobile layout to somebody signing in on a phone.

    A failure here is not an error for the person: they keep the display they
    had. So an unreachable or sleeping sandbox answers with no size rather
    than a status code the pane would have to special-case.

    Args:
        pod_id (None | Unset | UUID): The pod whose workspace to use. Each pod has its own, with
            its own files and browser. Omitted, your personal workspace.
        agent (None | Unset | UUID): A named agent of `pod_id`, to use that agent's workspace.
            Each agent has its own. Omitted, the pod assistant's.
        body (DisplaySizeRequest): The size the pane wants its picture to be, in CSS pixels.

            Bounded here because these numbers come from a browser window and decide
            how much memory a framebuffer takes. The sandbox clamps again against the
            framebuffer it actually allocated, which is the limit that cannot be
            argued with.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DisplaySizeResponse | ErrorResponse
    """

    return sync_detailed(
        client=client,
        body=body,
        pod_id=pod_id,
        agent=agent,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: DisplaySizeRequest,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> Response[DisplaySizeResponse | ErrorResponse]:
    """Fit the workspace display to the pane showing it

     Resize the sandbox display so the picture matches the pane.

    The alternative, and what this replaces, is one fixed display scaled to
    fit: a 3:2 screen letterboxed into whatever box it lands in, small and
    ringed with dead space. Resizing the display itself means the pixels sent
    are the pixels shown -- and a narrow pane gets a narrow *viewport*, so a
    site serves its mobile layout to somebody signing in on a phone.

    A failure here is not an error for the person: they keep the display they
    had. So an unreachable or sleeping sandbox answers with no size rather
    than a status code the pane would have to special-case.

    Args:
        pod_id (None | Unset | UUID): The pod whose workspace to use. Each pod has its own, with
            its own files and browser. Omitted, your personal workspace.
        agent (None | Unset | UUID): A named agent of `pod_id`, to use that agent's workspace.
            Each agent has its own. Omitted, the pod assistant's.
        body (DisplaySizeRequest): The size the pane wants its picture to be, in CSS pixels.

            Bounded here because these numbers come from a browser window and decide
            how much memory a framebuffer takes. The sandbox clamps again against the
            framebuffer it actually allocated, which is the limit that cannot be
            argued with.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DisplaySizeResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        body=body,
        pod_id=pod_id,
        agent=agent,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: DisplaySizeRequest,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> DisplaySizeResponse | ErrorResponse | None:
    """Fit the workspace display to the pane showing it

     Resize the sandbox display so the picture matches the pane.

    The alternative, and what this replaces, is one fixed display scaled to
    fit: a 3:2 screen letterboxed into whatever box it lands in, small and
    ringed with dead space. Resizing the display itself means the pixels sent
    are the pixels shown -- and a narrow pane gets a narrow *viewport*, so a
    site serves its mobile layout to somebody signing in on a phone.

    A failure here is not an error for the person: they keep the display they
    had. So an unreachable or sleeping sandbox answers with no size rather
    than a status code the pane would have to special-case.

    Args:
        pod_id (None | Unset | UUID): The pod whose workspace to use. Each pod has its own, with
            its own files and browser. Omitted, your personal workspace.
        agent (None | Unset | UUID): A named agent of `pod_id`, to use that agent's workspace.
            Each agent has its own. Omitted, the pod assistant's.
        body (DisplaySizeRequest): The size the pane wants its picture to be, in CSS pixels.

            Bounded here because these numbers come from a browser window and decide
            how much memory a framebuffer takes. The sandbox clamps again against the
            framebuffer it actually allocated, which is the limit that cannot be
            argued with.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DisplaySizeResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
            pod_id=pod_id,
            agent=agent,
        )
    ).parsed
