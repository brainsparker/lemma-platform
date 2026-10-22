from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.forget_response import ForgetResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    origin: str,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["origin"] = origin

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
        "method": "delete",
        "url": "/web-logins",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ForgetResponse | None:
    if response.status_code == 200:
        response_200 = ForgetResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ForgetResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    origin: str,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> Response[ErrorResponse | ForgetResponse]:
    """Sign your browser out of a site

    Args:
        origin (str): The site to forget, as an origin or a host.
        pod_id (None | Unset | UUID): The pod whose browser to manage. Omitted, your personal one.
        agent (None | Unset | UUID): A named agent of `pod_id`, to manage that agent's browser.
            Omitted, the pod assistant's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ForgetResponse]
    """

    kwargs = _get_kwargs(
        origin=origin,
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
    origin: str,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> ErrorResponse | ForgetResponse | None:
    """Sign your browser out of a site

    Args:
        origin (str): The site to forget, as an origin or a host.
        pod_id (None | Unset | UUID): The pod whose browser to manage. Omitted, your personal one.
        agent (None | Unset | UUID): A named agent of `pod_id`, to manage that agent's browser.
            Omitted, the pod assistant's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ForgetResponse
    """

    return sync_detailed(
        client=client,
        origin=origin,
        pod_id=pod_id,
        agent=agent,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    origin: str,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> Response[ErrorResponse | ForgetResponse]:
    """Sign your browser out of a site

    Args:
        origin (str): The site to forget, as an origin or a host.
        pod_id (None | Unset | UUID): The pod whose browser to manage. Omitted, your personal one.
        agent (None | Unset | UUID): A named agent of `pod_id`, to manage that agent's browser.
            Omitted, the pod assistant's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ForgetResponse]
    """

    kwargs = _get_kwargs(
        origin=origin,
        pod_id=pod_id,
        agent=agent,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    origin: str,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> ErrorResponse | ForgetResponse | None:
    """Sign your browser out of a site

    Args:
        origin (str): The site to forget, as an origin or a host.
        pod_id (None | Unset | UUID): The pod whose browser to manage. Omitted, your personal one.
        agent (None | Unset | UUID): A named agent of `pod_id`, to manage that agent's browser.
            Omitted, the pod assistant's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ForgetResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            origin=origin,
            pod_id=pod_id,
            agent=agent,
        )
    ).parsed
