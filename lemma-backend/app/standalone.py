"""Reusable single-process (API + embedded worker) assembly.

Lives inside the `app` package (editable when installed as a library) so
lemma-cloud's standalone app can import it reliably. The top-level
standalone_app.py is the OSS run target; lemma_cloud/standalone_app.py is the
cloud one -- both call build_standalone_app with their own api_app and module
list.

The embedded app *is* the whole deployment -- desktop, the Docker local stack
and `make dev` all launch `uvicorn local_app:app` and nothing else -- so it runs
every work class (``WORK_CLASSES`` is for split deployments of the standalone
worker), and it finishes the cutover from the old event transport itself
(``WORK_CUTOVER_AUTO_FINISH`` on, unless set explicitly): with one process
there is no previous release left running once this one serves. uvicorn owns the process's signals: the lifespan's exit is the stop,
and the work runtime drains within its per-class grace before the API's own
teardown closes what its handlers use.
"""

from __future__ import annotations

from collections.abc import Sequence
from collections.abc import Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import FastAPI

if TYPE_CHECKING:
    from app.core.registry.contract import LemmaModule


@dataclass(frozen=True)
class EmbeddedApp:
    """An ASGI sub-application whose lifespan belongs to the local process."""

    path: str
    app: FastAPI


def build_standalone_app(
    api_app: FastAPI,
    modules: Sequence[LemmaModule] | None = None,
    *,
    embedded_apps: Sequence[EmbeddedApp] = (),
) -> FastAPI:
    """Embed the work runtime into ``api_app`` for single-process deployments.

    ``modules`` is the list the worker half registers and runs (the open-source
    list when omitted); it must be the list ``api_app`` was built from.
    """
    api_app.state.embedded_worker = True
    api_app.state.embedded_apps = tuple(item.path for item in embedded_apps)
    api_lifespan = api_app.router.lifespan_context

    for item in embedded_apps:
        path = item.path.rstrip("/")
        if not path.startswith("/") or path == "":
            raise ValueError(f"embedded app path must be absolute: {item.path!r}")
        api_app.mount(path, item.app)

    @asynccontextmanager
    async def standalone_lifespan(app: FastAPI):
        from app.core.infrastructure.work.process import worker_services
        from app.core.registry.installed import OSS_MODULES

        async with AsyncExitStack() as stack:
            stack.enter_context(_single_process_cutover())
            await stack.enter_async_context(api_lifespan(app))
            for item in embedded_apps:
                await stack.enter_async_context(
                    item.app.router.lifespan_context(item.app)
                )
            await stack.enter_async_context(
                worker_services(
                    OSS_MODULES if modules is None else modules,
                    service_name="lemma-api",
                    embedded=True,
                )
            )
            yield

    api_app.router.lifespan_context = standalone_lifespan
    return api_app


@contextmanager
def _single_process_cutover() -> Iterator[None]:
    """``WORK_CUTOVER_AUTO_FINISH`` on for this process's life, unless the
    environment set it: the default (off) is for multi-process deployments."""
    from app.core.infrastructure.work.config import work_settings

    if "work_cutover_auto_finish" in work_settings.model_fields_set:
        yield
        return
    work_settings.work_cutover_auto_finish = True
    try:
        yield
    finally:
        work_settings.work_cutover_auto_finish = False
        work_settings.model_fields_set.discard("work_cutover_auto_finish")
