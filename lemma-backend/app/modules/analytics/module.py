"""Analytics module registration."""

from app.core.registry import LemmaModule


def _register_analytics() -> None:
    # Registers every projection on the after-commit analytics sink. Every
    # process: the API commits most domain events, the worker the rest.
    import app.modules.analytics.events  # noqa: F401


def _register_periodic_work() -> None:
    # Worker only: what the old event transport still owed, converted (cutover).
    import app.modules.analytics.events.legacy_cutover  # noqa: F401


module = LemmaModule(
    register_periodic_work=_register_periodic_work,
    name="analytics",
    register_analytics=_register_analytics,
)
