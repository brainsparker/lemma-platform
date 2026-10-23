from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.app_permissions_replace_request import AppPermissionsReplaceRequest
from ...models.app_permissions_response import AppPermissionsResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    app_name: str,
    *,
    body: AppPermissionsReplaceRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/pods/{pod_id}/apps/{app_name}/permissions".format(
            pod_id=quote(str(pod_id), safe=""),
            app_name=quote(str(app_name), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AppPermissionsResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AppPermissionsResponse.from_dict(response.json())

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
) -> Response[AppPermissionsResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    app_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AppPermissionsReplaceRequest,
) -> Response[AppPermissionsResponse | ErrorResponse]:
    """Replace App Grants

     An app's page acts for whoever views it, with that viewer's access intersected with these grants and
    never more. A call outside them is refused `403 APP_NOT_GRANTED`, naming the resource type, name and
    permission to grant here. Replacing them needs `app.update` on the app, and every grant must be one
    you could use yourself on that resource (`CONFERRAL_EXCEEDS_HOLDER` otherwise).

    Args:
        pod_id (UUID):
        app_name (str):
        body (AppPermissionsReplaceRequest): The app's declared grants: what its page may do, at
            most, for whoever
            views it. Replaces every grant it held; an empty list revokes them all.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AppPermissionsResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        app_name=app_name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    app_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AppPermissionsReplaceRequest,
) -> AppPermissionsResponse | ErrorResponse | None:
    """Replace App Grants

     An app's page acts for whoever views it, with that viewer's access intersected with these grants and
    never more. A call outside them is refused `403 APP_NOT_GRANTED`, naming the resource type, name and
    permission to grant here. Replacing them needs `app.update` on the app, and every grant must be one
    you could use yourself on that resource (`CONFERRAL_EXCEEDS_HOLDER` otherwise).

    Args:
        pod_id (UUID):
        app_name (str):
        body (AppPermissionsReplaceRequest): The app's declared grants: what its page may do, at
            most, for whoever
            views it. Replaces every grant it held; an empty list revokes them all.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AppPermissionsResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        app_name=app_name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    app_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AppPermissionsReplaceRequest,
) -> Response[AppPermissionsResponse | ErrorResponse]:
    """Replace App Grants

     An app's page acts for whoever views it, with that viewer's access intersected with these grants and
    never more. A call outside them is refused `403 APP_NOT_GRANTED`, naming the resource type, name and
    permission to grant here. Replacing them needs `app.update` on the app, and every grant must be one
    you could use yourself on that resource (`CONFERRAL_EXCEEDS_HOLDER` otherwise).

    Args:
        pod_id (UUID):
        app_name (str):
        body (AppPermissionsReplaceRequest): The app's declared grants: what its page may do, at
            most, for whoever
            views it. Replaces every grant it held; an empty list revokes them all.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AppPermissionsResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        app_name=app_name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    app_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AppPermissionsReplaceRequest,
) -> AppPermissionsResponse | ErrorResponse | None:
    """Replace App Grants

     An app's page acts for whoever views it, with that viewer's access intersected with these grants and
    never more. A call outside them is refused `403 APP_NOT_GRANTED`, naming the resource type, name and
    permission to grant here. Replacing them needs `app.update` on the app, and every grant must be one
    you could use yourself on that resource (`CONFERRAL_EXCEEDS_HOLDER` otherwise).

    Args:
        pod_id (UUID):
        app_name (str):
        body (AppPermissionsReplaceRequest): The app's declared grants: what its page may do, at
            most, for whoever
            views it. Replaces every grant it held; an empty list revokes them all.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AppPermissionsResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            app_name=app_name,
            client=client,
            body=body,
        )
    ).parsed
