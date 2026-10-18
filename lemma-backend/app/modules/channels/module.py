"""Channels module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.channels.api.controller import router as channels
    from app.modules.channels.api.message_controller import (
        inbox_router,
        router as lines,
    )

    # The inbox and search have fixed segments where a channel's id would go,
    # so they are matched first.
    return [inbox_router, channels, lines]


def _register_work() -> None:
    # Every process: a merge of two contacts (API, a web visitor proving an
    # email) writes this through `ContactMerged`.
    import app.modules.channels.events.contact_merged_work  # noqa: F401


module = LemmaModule(name="channels", routers=_routers, register_work=_register_work)
