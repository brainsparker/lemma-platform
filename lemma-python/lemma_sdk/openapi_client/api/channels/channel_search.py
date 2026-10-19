from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.search_response import SearchResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    q: str,
    channel_id: None | Unset | UUID = UNSET,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 30,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["q"] = q

    json_channel_id: None | str | Unset
    if isinstance(channel_id, Unset):
        json_channel_id = UNSET
    elif isinstance(channel_id, UUID):
        json_channel_id = str(channel_id)
    else:
        json_channel_id = channel_id
    params["channel_id"] = json_channel_id

    json_cursor: None | str | Unset
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/search".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SearchResponse | None:
    if response.status_code == 200:
        response_200 = SearchResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SearchResponse]:
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
    q: str,
    channel_id: None | Unset | UUID = UNSET,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 30,
) -> Response[ErrorResponse | SearchResponse]:
    """Search

     Lines matching every word, newest first, in the channels the reader
    sees -- or in one of them. An answer withheld from the reader is not found
    by what it said.

    Args:
        pod_id (UUID):
        q (str):
        channel_id (None | Unset | UUID):
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SearchResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        q=q,
        channel_id=channel_id,
        cursor=cursor,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    q: str,
    channel_id: None | Unset | UUID = UNSET,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 30,
) -> ErrorResponse | SearchResponse | None:
    """Search

     Lines matching every word, newest first, in the channels the reader
    sees -- or in one of them. An answer withheld from the reader is not found
    by what it said.

    Args:
        pod_id (UUID):
        q (str):
        channel_id (None | Unset | UUID):
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SearchResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        q=q,
        channel_id=channel_id,
        cursor=cursor,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    q: str,
    channel_id: None | Unset | UUID = UNSET,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 30,
) -> Response[ErrorResponse | SearchResponse]:
    """Search

     Lines matching every word, newest first, in the channels the reader
    sees -- or in one of them. An answer withheld from the reader is not found
    by what it said.

    Args:
        pod_id (UUID):
        q (str):
        channel_id (None | Unset | UUID):
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SearchResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        q=q,
        channel_id=channel_id,
        cursor=cursor,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    q: str,
    channel_id: None | Unset | UUID = UNSET,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 30,
) -> ErrorResponse | SearchResponse | None:
    """Search

     Lines matching every word, newest first, in the channels the reader
    sees -- or in one of them. An answer withheld from the reader is not found
    by what it said.

    Args:
        pod_id (UUID):
        q (str):
        channel_id (None | Unset | UUID):
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SearchResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            q=q,
            channel_id=channel_id,
            cursor=cursor,
            limit=limit,
        )
    ).parsed
