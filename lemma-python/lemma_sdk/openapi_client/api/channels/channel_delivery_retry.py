from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.delivery_response import DeliveryResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    delivery_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/deliveries/{delivery_id}/retry".format(
            pod_id=quote(str(pod_id), safe=""),
            channel_id=quote(str(channel_id), safe=""),
            message_id=quote(str(message_id), safe=""),
            delivery_id=quote(str(delivery_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DeliveryResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = DeliveryResponse.from_dict(response.json())

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
) -> Response[DeliveryResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    delivery_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[DeliveryResponse | ErrorResponse]:
    """Retry Delivery

     Try a failed or given-up send again, now: for who wrote the line, or
    may change the channel.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        delivery_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeliveryResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        message_id=message_id,
        delivery_id=delivery_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    delivery_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> DeliveryResponse | ErrorResponse | None:
    """Retry Delivery

     Try a failed or given-up send again, now: for who wrote the line, or
    may change the channel.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        delivery_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeliveryResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        channel_id=channel_id,
        message_id=message_id,
        delivery_id=delivery_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    delivery_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[DeliveryResponse | ErrorResponse]:
    """Retry Delivery

     Try a failed or given-up send again, now: for who wrote the line, or
    may change the channel.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        delivery_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DeliveryResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        message_id=message_id,
        delivery_id=delivery_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    delivery_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> DeliveryResponse | ErrorResponse | None:
    """Retry Delivery

     Try a failed or given-up send again, now: for who wrote the line, or
    may change the channel.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        delivery_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DeliveryResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            channel_id=channel_id,
            message_id=message_id,
            delivery_id=delivery_id,
            client=client,
        )
    ).parsed
