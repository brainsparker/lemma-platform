"""``python -m app.cutover_check``: how far is the cutover from the old transport?

Migration ``0063_drop_event_outbox`` queues the cutover sweep
(``app/core/infrastructure/cutover``), which takes over everything the old
event transport still owes and drops its tables once the previous release has
stopped. Nothing waits on this check -- the sweep needs no operator -- but it
says where the sweep is:

* whether ``domain_event_outbox`` / ``domain_event_inbox`` still exist, and how
  many deliveries each route is still owed in them;
* the sweep item (queued, running, dead, or absent once it finished) and the
  ``cutover.legacy_event`` items still queued, running or dead, per route;
* streaq jobs still in Redis, by function, and each legacy group's backlog
  (informational: what they owed is read from the outbox);
* a separately configured datastore database's leftover outbox, and the pod
  schemas the change-log backfill has not reached yet.

``finished`` is true once the tables are gone, the sweep has completed and no
converted delivery is left. Exits 0 unless the check itself fails;
``--json`` prints the report as JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


@dataclass
class Report:
    legacy_tables: bool = False
    owed: dict[str, int] = field(default_factory=dict)
    sweep: str = "absent"
    converted_left: dict[str, dict[str, int]] = field(default_factory=dict)
    streaq_jobs: dict[str, int] = field(default_factory=dict)
    legacy_groups: dict[str, int | None] = field(default_factory=dict)
    datastore_outbox: bool = False
    schemas_blocking_change_log: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return (
            not self.legacy_tables
            and self.sweep == "absent"
            and not self.converted_left
            and not self.datastore_outbox
        )


async def cutover_report(
    *,
    session_maker: Callable[[], AsyncSession] | None = None,
    datastore_engine: AsyncEngine | None = None,
) -> Report:
    """The report; the process's own database and Redis unless given others."""
    from app.core.infrastructure.db.session import async_session_maker
    from app.modules.datastore.infrastructure.session import get_datastore_engine

    report = Report()
    await _check_redis(report)
    await _check_main_database(report, session_maker or async_session_maker)
    await _check_datastore_database(report, datastore_engine or get_datastore_engine())
    return report


async def _check_redis(report: Report) -> None:
    from redis.exceptions import RedisError

    from app.core.infrastructure.cutover.legacy_streams import (
        LEGACY_GROUPS,
        group_backlog,
    )
    from app.core.infrastructure.cutover.streaq_jobs import jobs_by_function
    from app.core.infrastructure.redis.client import get_redis

    redis = get_redis(decode_responses=False)
    try:
        for stream, group in LEGACY_GROUPS:
            backlog = await group_backlog(redis, stream, group)
            if backlog is not None:
                report.legacy_groups[f"{stream}/{group}"] = backlog
        report.streaq_jobs = await jobs_by_function(redis)
    except (RedisError, OSError) as exc:
        report.notes.append(f"redis unreadable: {type(exc).__name__}")


async def _check_main_database(
    report: Report, session_maker: Callable[[], AsyncSession]
) -> None:
    from app.core.infrastructure.cutover.routes import ROUTES
    from app.core.infrastructure.cutover.takeover import (
        LEGACY_EVENT,
        LEGACY_SWEEP,
        count_owed,
    )
    from app.core.infrastructure.cutover.work import legacy_tables_present

    async with session_maker() as session:
        report.legacy_tables = await legacy_tables_present(session)
        if report.legacy_tables:
            for route in ROUTES:
                owed = await count_owed(session, route)
                if owed:
                    report.owed[route.consumer] = owed
        sweep = await session.scalar(
            text("SELECT state FROM work_items WHERE kind = :kind LIMIT 1"),
            {"kind": LEGACY_SWEEP},
        )
        report.sweep = str(sweep) if sweep else "absent"
        rows = await session.execute(
            text(
                "SELECT payload->>'route', state, count(*) FROM work_items "
                "WHERE kind = :kind GROUP BY 1, 2"
            ),
            {"kind": LEGACY_EVENT},
        )
        for route, state, count in rows.all():
            report.converted_left.setdefault(str(route), {})[str(state)] = int(count)
    if report.sweep == "dead":
        report.notes.append(
            "the sweep is dead: `python -m app.core.infrastructure.work.admin retry "
            f"--kind {LEGACY_SWEEP}` once its error is fixed"
        )


async def _check_datastore_database(report: Report, engine: AsyncEngine) -> None:
    from app.modules.datastore.changes.gate import schemas_blocking_switch

    async with engine.connect() as conn:
        present = bool(
            await conn.scalar(
                text("SELECT to_regclass('domain_event_outbox') IS NOT NULL")
            )
        )
    # On a shared database that table is the main outbox, reported above.
    report.datastore_outbox = present and not report.legacy_tables
    report.schemas_blocking_change_log = await schemas_blocking_switch(engine)


def _out(line: str) -> None:
    sys.stdout.write(line + "\n")


def main(argv: list[str] | None = None) -> int:
    from app.core.config import settings
    from app.core.log.log import setup_logging

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(settings.environment, service_name="lemma-cutover-check")
    report = asyncio.run(cutover_report())
    if args.json:
        _out(json.dumps({**asdict(report), "finished": report.finished}, indent=2))
        return 0
    backlog = {k: v for k, v in report.legacy_groups.items() if v}
    _out("finished" if report.finished else "in progress")
    _out(f"  legacy tables present: {report.legacy_tables}")
    _out(f"  still owed in the outbox, per route: {report.owed}")
    _out(f"  sweep: {report.sweep}")
    _out(f"  converted deliveries left: {report.converted_left}")
    _out(f"  streaq jobs in Redis: {report.streaq_jobs}")
    _out(f"  legacy groups' backlog: {backlog}")
    _out(f"  datastore database outbox left: {report.datastore_outbox}")
    _out(
        f"  pod schemas not yet on the change log: {report.schemas_blocking_change_log}"
    )
    for note in report.notes:
        _out(f"  note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
