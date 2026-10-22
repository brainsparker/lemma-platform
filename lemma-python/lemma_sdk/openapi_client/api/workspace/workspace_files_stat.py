from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.workspace_file_entry import WorkspaceFileEntry
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    path: str,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["path"] = path

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
        "method": "get",
        "url": "/workspace/files:stat",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | WorkspaceFileEntry | None:
    if response.status_code == 200:
        response_200 = WorkspaceFileEntry.from_dict(response.json())

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
) -> Response[ErrorResponse | WorkspaceFileEntry]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    path: str,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> Response[ErrorResponse | WorkspaceFileEntry]:
    """Stat one workspace file

    Args:
        path (str):
        pod_id (None | Unset | UUID): The pod whose workspace to use. Each pod has its own, with
            its own files and browser. Omitted, your personal workspace.
        agent (None | Unset | UUID): A named agent of `pod_id`, to use that agent's workspace.
            Each agent has its own. Omitted, the pod assistant's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WorkspaceFileEntry]
    """

    kwargs = _get_kwargs(
        path=path,
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
    path: str,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> ErrorResponse | WorkspaceFileEntry | None:
    """Stat one workspace file

    Args:
        path (str):
        pod_id (None | Unset | UUID): The pod whose workspace to use. Each pod has its own, with
            its own files and browser. Omitted, your personal workspace.
        agent (None | Unset | UUID): A named agent of `pod_id`, to use that agent's workspace.
            Each agent has its own. Omitted, the pod assistant's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WorkspaceFileEntry
    """

    return sync_detailed(
        client=client,
        path=path,
        pod_id=pod_id,
        agent=agent,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    path: str,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> Response[ErrorResponse | WorkspaceFileEntry]:
    """Stat one workspace file

    Args:
        path (str):
        pod_id (None | Unset | UUID): The pod whose workspace to use. Each pod has its own, with
            its own files and browser. Omitted, your personal workspace.
        agent (None | Unset | UUID): A named agent of `pod_id`, to use that agent's workspace.
            Each agent has its own. Omitted, the pod assistant's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WorkspaceFileEntry]
    """

    kwargs = _get_kwargs(
        path=path,
        pod_id=pod_id,
        agent=agent,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    path: str,
    pod_id: None | Unset | UUID = UNSET,
    agent: None | Unset | UUID = UNSET,
) -> ErrorResponse | WorkspaceFileEntry | None:
    """Stat one workspace file

    Args:
        path (str):
        pod_id (None | Unset | UUID): The pod whose workspace to use. Each pod has its own, with
            its own files and browser. Omitted, your personal workspace.
        agent (None | Unset | UUID): A named agent of `pod_id`, to use that agent's workspace.
            Each agent has its own. Omitted, the pod assistant's.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WorkspaceFileEntry
    """

    return (
        await asyncio_detailed(
            client=client,
            path=path,
            pod_id=pod_id,
            agent=agent,
        )
    ).parsed
