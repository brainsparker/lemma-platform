"""Work-table gauges: how much is waiting, and how long the oldest has waited.

A rate graph cannot tell a worker with nothing to do from one that has stopped
doing it. ``ready_oldest_age`` can, which is why it is the alerting signal per
class. Sampled by one query pair every interval rather than in an observable
callback, because the callbacks are synchronous and the reading is I/O. The
table holds only live work, so the scan is over what is actually pending.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.work.execute import SessionMaker
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)

_TOP_NAMESPACES = 20

_BY_CLASS = text(
    """
    SELECT class,
           count(*) FILTER (WHERE state = 'queued') AS queued,
           count(*) FILTER (WHERE state = 'running') AS running,
           count(*) FILTER (WHERE state = 'dead') AS dead,
           EXTRACT(EPOCH FROM now() - min(run_at)
                   FILTER (WHERE state = 'queued' AND run_at <= now())) AS oldest
      FROM work_items
     GROUP BY class
    """
)
_TOP_NS = text(
    """
    SELECT class, ns, state, count(*) AS n
      FROM work_items
     WHERE state IN ('queued', 'running')
     GROUP BY class, ns, state
     ORDER BY n DESC
     LIMIT :limit
    """
)


@dataclass
class _Snapshot:
    """Last successful reading. Empty means "not measured", never zero."""

    oldest: dict[str, float] = field(default_factory=dict)
    by_class: dict[tuple[str, str], int] = field(default_factory=dict)
    by_ns: dict[tuple[str, str, str], int] = field(default_factory=dict)

    def forget(self) -> None:
        self.oldest, self.by_class, self.by_ns = {}, {}, {}


_snapshot = _Snapshot()
_incident = DependencyIncident("work.gauges", logger=logger)


def _observe_oldest(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [Observation(age, {"class": cls}) for cls, age in _snapshot.oldest.items()]


def _observe_state(
    state: str,
) -> Callable[[CallbackOptions], Iterable[Observation]]:
    def observe(options: CallbackOptions) -> Iterable[Observation]:
        del options
        return [
            Observation(n, {"class": cls})
            for (cls, s), n in _snapshot.by_class.items()
            if s == state
        ]

    return observe


def _observe_by_ns(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [
        Observation(n, {"class": cls, "ns": ns, "state": state})
        for (cls, ns, state), n in _snapshot.by_ns.items()
    ]


meter.create_observable_gauge(
    "lemma.work.ready_oldest_age",
    callbacks=[_observe_oldest],
    unit="s",
    description="Age of the oldest due, unclaimed item per class.",
)
for _state in ("queued", "running", "dead"):
    meter.create_observable_gauge(
        f"lemma.work.{_state}",
        callbacks=[_observe_state(_state)],
        description=f"Work items in state {_state}, per class.",
    )
meter.create_observable_gauge(
    "lemma.work.by_namespace",
    callbacks=[_observe_by_ns],
    description="Queued and running items for the busiest namespaces.",
)


async def sample(session_maker: SessionMaker) -> None:
    async with session_maker() as session:
        by_class = (await session.execute(_BY_CLASS)).all()
        top = (await session.execute(_TOP_NS, {"limit": _TOP_NAMESPACES * 2})).all()
    _snapshot.oldest = {
        row._mapping["class"]: float(row.oldest)
        for row in by_class
        if row.oldest is not None
    }
    counts: dict[tuple[str, str], int] = {}
    for row in by_class:
        cls = row._mapping["class"]
        for state in ("queued", "running", "dead"):
            counts[(cls, state)] = int(row._mapping[state])
    _snapshot.by_class = counts
    _snapshot.by_ns = {(r._mapping["class"], r.ns, r.state): int(r.n) for r in top}


async def gauge_loop(session_maker: SessionMaker, interval_seconds: float) -> None:
    while True:
        try:
            await sample(session_maker)
            _incident.record_success()
        except (SQLAlchemyError, OSError) as exc:
            _snapshot.forget()
            _incident.record_failure(error_type=type(exc).__name__)
        await asyncio.sleep(interval_seconds)
