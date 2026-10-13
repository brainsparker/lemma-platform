"""The process's shared outbound HTTP client, with one call's timeout.

Each platform call used to build its own ``httpx.AsyncClient`` and tear it
down again, so no connection to Telegram, Graph or Slack's upload host was ever
reused. `app.core.net.http_client` is the one pooled client; this hands it out
for a block with the timeout the call always had, applied per request because
the client itself is shared. The block closes nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx

from app.core.net.http_client import get_shared_http_client

# What a request body may carry: JSON, form fields, or upload parts, as httpx
# takes them.
JsonBody = Mapping[str, object] | list[object]
FormBody = Mapping[str, str]
FileParts = Mapping[str, tuple[str, bytes] | tuple[str, bytes, str]]


class TimedHttp:
    """The shared client, sending every request with this block's timeout."""

    def __init__(self, client: httpx.AsyncClient, timeout: float) -> None:
        self._client = client
        self._timeout = timeout

    async def get(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> httpx.Response:
        return await self._client.get(url, headers=headers, timeout=self._timeout)

    async def post(
        self,
        url: str,
        *,
        json: JsonBody | None = None,
        data: FormBody | None = None,
        files: FileParts | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return await self._client.post(
            url,
            json=json,
            data=data,
            files=files,
            headers=headers,
            timeout=self._timeout,
        )

    def stream(
        self, method: str, url: str, *, headers: Mapping[str, str] | None = None
    ) -> AbstractAsyncContextManager[httpx.Response]:
        return self._client.stream(method, url, headers=headers, timeout=self._timeout)


@asynccontextmanager
async def outbound_http(timeout: float) -> AsyncIterator[TimedHttp]:
    yield TimedHttp(get_shared_http_client(), timeout)
