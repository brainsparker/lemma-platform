"""``python -m app.cutover_check``: is the old transport drained? (design §13 rule 4)

Migration ``0063_drop_event_outbox`` removes what is left of the old event
transport: the ``domain_event_outbox`` and ``domain_event_inbox`` tables, and
with the release that carries it, the cutover converters and the datastore
outbox write path. Run this against the deployment **before** deploying that
release, with the previous release's image (it ships in both). It exits 0 only
when nothing the old transport held is still owed:

* every *owed* legacy consumer group -- one whose unfinished entries are work
  nothing else holds (``cutover/legacy_streams.OWED_GROUPS``) -- is gone or
  has nothing pending or unread (its converter destroys it when done);
* no streaq job is left in Redis;
* no converter (``changes.drain``, ``surfaces.drain_events``,
  ``workflow.drain_schedule_fires``) is queued, running or dead;
* the main outbox has no unpublished row of an owed type, and the datastore
  database's outbox has no record event;
* record writes are on the change log everywhere (no pod schema blocks the
  switch).

Other groups' backlogs (analytics, and flows whose work was seeded from domain
state) and other unpublished outbox rows are reported, not gated: nothing reads
them any more. ``--json`` prints the report as JSON.

The migration repeats the database half of this (it cannot reach Redis or a
separate datastore database) and refuses to run while it fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field

from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

CONVERTER_KINDS = (
    "changes.drain",
    "surfaces.drain_events",
    "workflow.drain_schedule_fires",
)


@dataclass
class Report:
    owed_groups: dict[str, int | None] = field(default_factory=dict)
    other_groups: dict[str, int | None] = field(default_factory=dict)
    streaq_jobs: dict[str, int] = field(default_factory=dict)
    converters: dict[str, int] = field(default_factory=dict)
    main_outbox_owed: int = 0
    main_outbox_other: int = 0
    datastore_outbox_records: int = 0
    schemas_blocking_change_log: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.problems


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
    from app.core.infrastructure.cutover.legacy_streams import (
        LEGACY_GROUPS,
        OWED_GROUPS,
        group_backlog,
    )
    from app.core.infrastructure.cutover.streaq_jobs import jobs_by_function
    from app.core.infrastructure.redis.client import get_redis

    redis = get_redis(decode_responses=False)
    for stream, group in LEGACY_GROUPS:
        backlog = await group_backlog(redis, stream, group)
        name = f"{stream}/{group}"
        converter = OWED_GROUPS.get((stream, group))
        if converter is None:
            report.other_groups[name] = backlog
            continue
        report.owed_groups[name] = backlog
        if backlog:
            report.problems.append(
                f"{name}: {backlog} entries not drained (converter {converter})"
            )
    report.streaq_jobs = await jobs_by_function(redis)
    if report.streaq_jobs:
        report.problems.append(f"streaq jobs left in Redis: {report.streaq_jobs}")


async def _check_main_database(
    report: Report, session_maker: Callable[[], AsyncSession]
) -> None:
    from app.core.infrastructure.cutover.legacy_streams import OWED_OUTBOX_TYPES

    async with session_maker() as session:
        counts = await session.execute(
            text(
                "SELECT kind, count(*) FROM work_items "
                "WHERE kind = ANY(:kinds) GROUP BY kind"
            ),
            {"kinds": list(CONVERTER_KINDS)},
        )
        report.converters = dict.fromkeys(CONVERTER_KINDS, 0) | {
            kind: int(count) for kind, count in counts.all()
        }
        unpublished: list[tuple[bool, int]] = []
        if await session.scalar(
            text("SELECT to_regclass('domain_event_outbox') IS NOT NULL")
        ):
            rows = await session.execute(
                text(
                    "SELECT event_type = ANY(:owed) AS owed, count(*) "
                    "FROM domain_event_outbox WHERE published_at IS NULL "
                    "AND dead_lettered_at IS NULL GROUP BY 1"
                ),
                {"owed": list(OWED_OUTBOX_TYPES)},
            )
            unpublished = [(bool(owed), int(count)) for owed, count in rows.all()]
    for kind, count in report.converters.items():
        if count:
            report.problems.append(
                f"converter {kind} has {count} item(s) queued, running or dead"
            )
    for owed, count in unpublished:
        if owed:
            report.main_outbox_owed = count
            report.problems.append(f"main outbox: {count} unpublished owed event(s)")
        else:
            report.main_outbox_other = count


async def _check_datastore_database(report: Report, engine: AsyncEngine) -> None:
    from app.modules.datastore.changes.gate import schemas_blocking_switch

    async with engine.connect() as conn:
        if await conn.scalar(
            text("SELECT to_regclass('domain_event_outbox') IS NOT NULL")
        ):
            report.datastore_outbox_records = int(
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM domain_event_outbox "
                        "WHERE event_type LIKE 'datastore.record.%' "
                        "AND published_at IS NULL AND dead_lettered_at IS NULL"
                    )
                )
                or 0
            )
    if report.datastore_outbox_records:
        report.problems.append(
            f"datastore outbox: {report.datastore_outbox_records} record "
            "event(s) not yet in a change log"
        )
    report.schemas_blocking_change_log = await schemas_blocking_switch(engine)
    if report.schemas_blocking_change_log:
        report.problems.append(
            f"{report.schemas_blocking_change_log} pod schema(s) not yet on the "
            "change log (pod_schema.migrate backfill still running)"
        )


def _out(line: str) -> None:
    sys.stdout.write(line + "\n")


def main(argv: list[str] | None = None) -> int:
    from app.core.log.log import setup_logging
    from app.core.config import settings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(settings.environment, service_name="lemma-cutover-check")
    report = asyncio.run(cutover_report())
    if args.json:
        _out(json.dumps({**asdict(report), "ready": report.ready}, indent=2))
    else:
        _out("ready" if report.ready else "NOT READY")
        for problem in report.problems:
            _out(f"  - {problem}")
        _out(f"  owed groups: {report.owed_groups}")
        _out(f"  converters live: {report.converters}")
        _out(
            f"  unpublished outbox rows not gated: {report.main_outbox_other}; "
            f"other groups' backlog (not gated): "
            f"{ {k: v for k, v in report.other_groups.items() if v} }"
        )
    return 0 if report.ready else 1


if __name__ == "__main__":
    sys.exit(main())
