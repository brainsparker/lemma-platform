"""A pod hub's start: the task that runs the hub, and what its waiters hear.

Every socket for a pod waits on its hub reading a starting position. Whatever
ends that start -- an outage, a bug, a stop -- has to reach those waiters, and
the wait is bounded, so a socket is refused rather than left hanging. A hub
whose task has ended starts again on the next socket instead of refusing the
pod until it is reaped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.request_context import create_background_task
from app.modules.datastore.domain.live import LiveCursorExpired, LiveHubUnavailable

#: How long a socket waits for its pod's hub to read its starting position.
START_TIMEOUT = 10.0
#: Start failures a socket reports as themselves; anything else is
#: ``LiveHubUnavailable``.
_EXPECTED = (SQLAlchemyError, OSError, LiveCursorExpired)


class HubStart:
    def __init__(self, name: str) -> None:
        self._name = name
        self.task: asyncio.Task[None] | None = None
        self._started = asyncio.Event()
        self._error: BaseException | None = None

    def launch(self, run: Callable[[], Coroutine[Any, Any, None]]) -> bool:
        """Run the hub unless it is running; True when it never ran before."""
        if self.task is not None and not self.task.done():
            return False
        first = self.task is None
        self._started = asyncio.Event()
        self._error = None
        self.task = create_background_task(run(), name=self._name)
        return first

    async def until_started(self) -> None:
        started = self._started
        try:
            await asyncio.wait_for(started.wait(), timeout=START_TIMEOUT)
        except TimeoutError as exc:
            raise LiveHubUnavailable(
                "the pod's live feed took too long to start"
            ) from exc
        if self._error is not None:
            raise self._error

    def succeeded(self) -> None:
        self._started.set()

    def failed(self, exc: BaseException) -> None:
        self._error = (
            exc
            if isinstance(exc, _EXPECTED)
            else LiveHubUnavailable("the pod's live feed did not start")
        )
        self._started.set()

    def take(self) -> asyncio.Task[None] | None:
        task, self.task = self.task, None
        return task
