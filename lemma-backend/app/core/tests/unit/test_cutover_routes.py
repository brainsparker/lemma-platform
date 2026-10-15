"""The cutover's route table is complete and every route has a converter.

Each old consumer that owed work nothing seeds from domain state is a route;
a worker that boots without a converter for one would leave that delivery
dead. The analytics routes must name exactly the event types the analytics
projections take, or an owed analytics event would be projected by nobody (or
twice).
"""

from __future__ import annotations

from collections import Counter

import pytest

from app.core.infrastructure.cutover.routes import (
    ROUTES,
    STATE_ROUTES,
    STREAQ_ROUTES,
    STREAQ_SEEDED,
    all_route_names,
    converter_for,
)


def _registered() -> None:
    from app.core.infrastructure.work.process import register_worker_modules
    from app.core.registry.installed import OSS_MODULES

    register_worker_modules(OSS_MODULES)


def test_every_route_has_a_converter_in_a_worker() -> None:
    _registered()
    assert [name for name in all_route_names() if converter_for(name) is None] == []


def test_route_names_are_unique_and_streaq_lists_disjoint() -> None:
    names = Counter(all_route_names())
    assert [name for name, n in names.items() if n > 1] == []
    assert not set(STREAQ_ROUTES) & set(STREAQ_SEEDED)
    assert all(name.startswith("state:") for name in STATE_ROUTES)


def test_analytics_routes_cover_exactly_the_projected_event_types() -> None:
    _registered()
    from app.core.analytics.domain_events import projected_event_types

    routed = [
        event_type
        for route in ROUTES
        if route.consumer.startswith("analytics.")
        for event_type in route.types
    ]
    assert len(routed) == len(set(routed)), "an event owed to two analytics routes"
    # The memory-brief cache invalidation is a projection too, but its old
    # consumer had no inbox and a TTL backstop: nothing is owed to it.
    memory_brief_only = {"datastore.file.updated", "datastore.file.deleted"}
    assert set(routed) == set(projected_event_types()) - memory_brief_only


def test_every_route_matches_something() -> None:
    for route in ROUTES:
        assert route.types or route.like, route.consumer


def test_a_deployment_registers_its_own_retired_streaq_jobs() -> None:
    from app.core.infrastructure.cutover import routes

    try:
        routes.retire_streaq_jobs("cloud_only_cron")
        assert "cloud_only_cron" in routes.retired_streaq_jobs()
        assert set(STREAQ_SEEDED) <= routes.retired_streaq_jobs()
    finally:
        routes._retired_streaq.discard("cloud_only_cron")  # noqa: SLF001
    with pytest.raises(ValueError):
        routes.retire_streaq_jobs(STREAQ_ROUTES[0])


def test_the_sweeps_per_route_counts_survive_the_logging_contract() -> None:
    """A dict field below error is dropped (``dropped_fields``); counts go as text."""
    from app.core.infrastructure.cutover.work import _counts
    from app.core.log.log import _bound_fields

    for event, field in (
        ("cutover.legacy_sweep.taken_over", "routes"),
        ("cutover.legacy_sweep.streaq_unconverted", "functions"),
    ):
        logged = _bound_fields(
            {"event": event, "level": "info", field: _counts({"b": 2, "a": 1, "z": 0})}
        )
        assert "dropped_fields" not in logged
        assert logged[field] == "a=1,b=2"
