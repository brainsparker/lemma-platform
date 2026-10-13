"""Handler and periodic registrations: what each kind is and how it runs.

A kind's class, retry policy, concurrency limit and lease live here, not on the
enqueue call, so a writer cannot put a kind in the wrong class and two writers
cannot disagree about its limits. The registry is filled at import time by
``@work_handler``; a dispatcher only claims kinds registered in its own process,
so an old worker in a rolling deploy never claims a kind it cannot run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, overload

from pydantic import BaseModel

from app.core.infrastructure.work.config import CLASS_DEFAULTS
from app.core.infrastructure.work.retry import DEFAULT_RETRY, RetryPolicy
from app.core.infrastructure.work.types import JsonObject, WorkClass, WorkMode

if TYPE_CHECKING:
    from app.core.infrastructure.work.context import DeadContext, FastContext
    from app.core.infrastructure.work.job import JobContext

type FastHandler[P: BaseModel] = Callable[["FastContext", P], Awaitable[None]]
type JobHandler[P: BaseModel] = Callable[["JobContext", P], Awaitable[None]]
type DeadHook = Callable[["DeadContext"], Awaitable[None]]

PERIODIC_PREFIX = "periodic:"


class UnknownWorkKind(LookupError):
    """No handler for this kind is registered in this process."""


@dataclass(frozen=True, slots=True)
class HandlerSpec:
    kind: str
    cls: WorkClass
    mode: WorkMode
    payload_type: type[BaseModel]
    #: The decorated coroutine function. Its context type follows ``mode``.
    handler: Callable[..., Awaitable[None]]
    retry: RetryPolicy
    lease: timedelta
    #: Max running items sharing one concurrency key; None = keys not allowed.
    concurrency: int | None
    on_dead: DeadHook | None
    priority: int


@dataclass(frozen=True, slots=True)
class PeriodicSpec:
    name: str
    every: timedelta
    kind: str
    payload: JsonObject = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        return f"{PERIODIC_PREFIX}{self.name}"


class WorkRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, HandlerSpec] = {}
        self._periodics: dict[str, PeriodicSpec] = {}

    def register(self, spec: HandlerSpec) -> None:
        existing = self._handlers.get(spec.kind)
        if existing is not None and existing.handler is not spec.handler:
            raise ValueError(f"work kind {spec.kind!r} is already registered")
        if spec.concurrency is not None and spec.concurrency < 1:
            raise ValueError(f"work kind {spec.kind!r}: concurrency must be >= 1")
        self._handlers[spec.kind] = spec

    @overload
    def handler[P: BaseModel](
        self,
        *,
        kind: str,
        cls: WorkClass,
        mode: Literal[WorkMode.FAST],
        payload: type[P],
        retry: RetryPolicy = ...,
        lease: timedelta | None = ...,
        concurrency: int | None = ...,
        on_dead: DeadHook | None = ...,
        priority: int = ...,
    ) -> Callable[[FastHandler[P]], FastHandler[P]]: ...

    @overload
    def handler[P: BaseModel](
        self,
        *,
        kind: str,
        cls: WorkClass,
        mode: Literal[WorkMode.JOB],
        payload: type[P],
        retry: RetryPolicy = ...,
        lease: timedelta | None = ...,
        concurrency: int | None = ...,
        on_dead: DeadHook | None = ...,
        priority: int = ...,
    ) -> Callable[[JobHandler[P]], JobHandler[P]]: ...

    def handler[P: BaseModel](
        self,
        *,
        kind: str,
        cls: WorkClass,
        mode: WorkMode,
        payload: type[P],
        retry: RetryPolicy = DEFAULT_RETRY,
        lease: timedelta | None = None,
        concurrency: int | None = None,
        on_dead: DeadHook | None = None,
        priority: int = 0,
    ) -> Callable[[Callable[..., Awaitable[None]]], Callable[..., Awaitable[None]]]:
        """Register the decorated coroutine as the handler for ``kind``."""

        def decorate(
            fn: Callable[..., Awaitable[None]],
        ) -> Callable[..., Awaitable[None]]:
            self.register(
                HandlerSpec(
                    kind=kind,
                    cls=cls,
                    mode=mode,
                    payload_type=payload,
                    handler=fn,
                    retry=retry,
                    lease=lease or CLASS_DEFAULTS[cls].lease,
                    concurrency=concurrency,
                    on_dead=on_dead,
                    priority=priority,
                )
            )
            return fn

        return decorate

    def periodic(
        self,
        name: str,
        every: timedelta,
        kind: str,
        payload: JsonObject | None = None,
    ) -> PeriodicSpec:
        """Run ``kind`` every ``every``, starting at the next worker boot.

        The kind must be registered (now or before boot). Each run enqueues the
        next in its own completion transaction; a periodic that goes dead stops
        until the next boot recreates it, and says so in the log.
        """
        if every <= timedelta(0):
            raise ValueError("periodic interval must be positive")
        spec = PeriodicSpec(name, every, kind, dict(payload or {}))
        existing = self._periodics.get(name)
        if existing is not None and existing != spec:
            raise ValueError(f"periodic {name!r} is already registered differently")
        self._periodics[name] = spec
        return spec

    def get(self, kind: str) -> HandlerSpec:
        try:
            return self._handlers[kind]
        except KeyError:
            raise UnknownWorkKind(kind) from None

    def find(self, kind: str) -> HandlerSpec | None:
        return self._handlers.get(kind)

    def kinds_for(self, cls: WorkClass) -> list[str]:
        return sorted(k for k, spec in self._handlers.items() if spec.cls is cls)

    def periodics(self) -> list[PeriodicSpec]:
        return list(self._periodics.values())

    def periodic_for(self, dedup_key: str | None) -> PeriodicSpec | None:
        if dedup_key is None or not dedup_key.startswith(PERIODIC_PREFIX):
            return None
        return self._periodics.get(dedup_key.removeprefix(PERIODIC_PREFIX))


#: The process's registry, filled by ``@work_handler`` at import time.
registry = WorkRegistry()
work_handler = registry.handler


def periodic(
    name: str, every: timedelta, kind: str, payload: JsonObject | None = None
) -> PeriodicSpec:
    return registry.periodic(name, every, kind, payload)
