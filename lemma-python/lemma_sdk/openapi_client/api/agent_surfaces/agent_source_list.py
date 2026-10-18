from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.source_list_response import SourceListResponse
from ...types import UNSET, Response


def _get_kwargs(
    pod_id: UUID,
    *,
    surface_name: str,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["surface_name"] = surface_name

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/sources".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SourceListResponse | None:
    if response.status_code == 200:
        response_200 = SourceListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SourceListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    surface_name: str,
) -> Response[ErrorResponse | SourceListResponse]:
    """List Sources

     Every group one bot is in, with each one's settings, newest first.

    Args:
        pod_id (UUID):
        surface_name (str): The bot's surface name.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SourceListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        surface_name=surface_name,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    surface_name: str,
) -> ErrorResponse | SourceListResponse | None:
    """List Sources

     Every group one bot is in, with each one's settings, newest first.

    Args:
        pod_id (UUID):
        surface_name (str): The bot's surface name.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SourceListResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        surface_name=surface_name,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    surface_name: str,
) -> Response[ErrorResponse | SourceListResponse]:
    """List Sources

     Every group one bot is in, with each one's settings, newest first.

    Args:
        pod_id (UUID):
        surface_name (str): The bot's surface name.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SourceListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        surface_name=surface_name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    surface_name: str,
) -> ErrorResponse | SourceListResponse | None:
    """List Sources

     Every group one bot is in, with each one's settings, newest first.

    Args:
        pod_id (UUID):
        surface_name (str): The bot's surface name.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SourceListResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            surface_name=surface_name,
        )
    ).parsed
