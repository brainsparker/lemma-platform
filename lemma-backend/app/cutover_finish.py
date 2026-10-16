"""``python -m app.cutover_finish``: drop the old event transport, once it owes nothing.

The operator's half of the cutover where ``WORK_CUTOVER_AUTO_FINISH`` is off
(the default: a multi-process deployment with rolling deploys; the
single-process app and the self-hosted Compose worker turn it on and let the
cutover sweep do this itself). It refuses -- exit 1, saying why -- unless what
``python -m app.cutover_check`` reports says the old transport is done with:

* the sweep has run its one-time conversion (the previous release had
  stopped writing), or there is no sweep left;
* nothing is owed in ``domain_event_outbox`` to any route;
* no streaq job is left in Redis, and Redis could be read to say so.

Then it runs ``finish`` (``app/core/infrastructure/cutover/work.py``): takes
over from the sweep's watermark outside any lock until a pass takes fewer than
1,000 deliveries; takes over the rest of a separately configured datastore
database's outbox under that outbox's lock and drops it (its rows are checked
against the main inbox, so it goes first); locks ``domain_event_outbox`` and
``domain_event_inbox`` with a 5 s ``lock_timeout``, takes over the last rows
and drops both in one transaction, retrying a lock it cannot get with backoff
a bounded number of times; then retires the legacy Redis groups and streams. Exit 0 once all of that is done,
2 when a lock was never granted (nothing is lost; run it again). Safe to run
again at any point; once finished it does nothing.

Migration ``0057_drop_old_transport`` never drops either table, even empty (a
previous release still running may write them); this, or the sweep with
``WORK_CUTOVER_AUTO_FINISH``, is what drops them.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.infrastructure.cutover.takeover import LEGACY_SWEEP
from app.core.infrastructure.cutover.work import SweepState
from app.cutover_check import Report, cutover_report

EXIT_REFUSED = 1
EXIT_LOCK_NOT_GRANTED = 2


def refusals(report: Report) -> list[str]:
    """Why the old transport cannot be dropped yet; empty when it can."""
    reasons = []
    if report.sweep != "absent" and not report.sweep_converted:
        reasons.append(
            f"the cutover sweep has not converted yet (sweep: {report.sweep}); "
            "the previous release may still be writing the old tables"
        )
    if report.owed:
        reasons.append(f"deliveries still owed in the outbox: {report.owed}")
    if any(note.startswith("redis unreadable") for note in report.notes):
        reasons.append("redis could not be read, so streaq leftovers are unknown")
    if report.streaq_jobs:
        reasons.append(f"streaq jobs still in Redis: {report.streaq_jobs}")
    return reasons


async def _sweep_state(session_maker: Callable[[], AsyncSession]) -> SweepState:
    """The sweep's watermark and cursors (defaults when it has finished)."""
    async with session_maker() as session:
        payload = await session.scalar(
            text("SELECT payload FROM work_items WHERE kind = :kind LIMIT 1"),
            {"kind": LEGACY_SWEEP},
        )
    return SweepState.model_validate(payload or {})


async def run(
    *,
    session_maker: Callable[[], AsyncSession] | None = None,
    datastore_engine: AsyncEngine | None = None,
    attempts: int = 5,
    lock_timeout_ms: int | None = None,
    out: Callable[[str], None] = lambda line: sys.stdout.write(line + "\n"),
) -> int:
    """The process's own database and Redis unless given others; the exit code."""
    from app.core.infrastructure.cutover.work import LOCK_TIMEOUT_MS, finish
    from app.core.infrastructure.db.session import async_session_maker

    sessions = session_maker or async_session_maker
    report = await cutover_report(
        session_maker=sessions, datastore_engine=datastore_engine
    )
    reasons = refusals(report)
    if reasons:
        out("refused: the old transport is not done with yet")
        for reason in reasons:
            out(f"  {reason}")
        out("  see `python -m app.cutover_check`; run this again once it is")
        return EXIT_REFUSED
    state = await _sweep_state(sessions)
    result = await finish(
        sessions,
        since=state.since,
        cursors=state.cursors,
        horizon=state.horizon,
        attempts=attempts,
        lock_timeout_ms=lock_timeout_ms or LOCK_TIMEOUT_MS,
    )
    out(f"taken over on the way: {result.taken}")
    if result.lock_timeouts:
        out(f"lock not granted in time: {result.lock_timeouts} attempt(s)")
    if not result.done:
        if not result.dropped:
            out(
                "not finished: domain_event_outbox / domain_event_inbox stay; "
                "something still holds them -- run this again"
            )
        for name in result.outboxes_left:
            out(f"not finished: the {name} database's outbox stays; run this again")
        return EXIT_LOCK_NOT_GRANTED
    out("finished: the old transport's tables and streams are gone")
    return 0


def main(argv: list[str] | None = None) -> int:
    from app.core.config import settings
    from app.core.log.log import setup_logging

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--attempts",
        type=int,
        default=5,
        help="lock attempts before giving up (each waits at most 5 s)",
    )
    args = parser.parse_args(argv)
    setup_logging(settings.environment, service_name="lemma-cutover-finish")
    return asyncio.run(run(attempts=max(1, args.attempts)))


if __name__ == "__main__":
    sys.exit(main())
