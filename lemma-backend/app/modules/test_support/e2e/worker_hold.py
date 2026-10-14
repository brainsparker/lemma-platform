"""Hold the session's real worker still while a test drives the work runtime itself.

A session-scoped worker subprocess (``worker``, ``document_worker``, any
``production_worker_process``) runs the full work runtime against the same
database every e2e test in the pytest process uses. A test that claims and runs
items in-process -- ``run_until_idle``, ``make_due``, a hand-built dispatcher --
to observe each transition races that worker for the same rows: the worker takes
the item, runs it without the test's doubles, and the test sees nothing. Alone,
the module passes, because nothing started the worker; packed with any module
that did, it fails.

Such a test module says so with ``pytestmark = pytest.mark.drives_work``; the
root conftest then holds every registered worker for the length of each of its
tests. A held worker is stopped only between transactions -- one frozen inside a
claim would keep queue rows locked and the test's own claim would skip them --
and it always resumes, so the next test that needs it finds it running.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import psycopg

from app.modules.test_support.e2e.waiters import eventually


@dataclass(frozen=True, slots=True)
class _Worker:
    pid: int
    database_url: str


_WORKERS: dict[int, _Worker] = {}

_SETTLE_SECONDS = 0.05


def register_session_worker(pid: int, database_url: str) -> None:
    """Record a worker subprocess this pytest process started, so holds can find it."""
    _WORKERS[pid] = _Worker(pid=pid, database_url=database_url)


def unregister_session_worker(pid: int) -> None:
    _WORKERS.pop(pid, None)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _client_ports(pid: int) -> list[int]:
    listing = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-i", "TCP", "-n", "-P", "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    ports: list[int] = []
    for line in listing.splitlines():
        # ``n127.0.0.1:54012->127.0.0.1:33201``: the left side is the worker's.
        if line.startswith("n") and "->" in line:
            local = line[1:].split("->", 1)[0]
            ports.append(int(local.rsplit(":", 1)[1]))
    return ports


def _libpq_url(database_url: str) -> str:
    scheme, rest = database_url.split("://", 1)
    return f"{scheme.split('+', 1)[0]}://{rest}"


async def _in_transaction(database_url: str, ports: list[int]) -> bool:
    """Whether any of these connections is inside a statement or transaction now.

    ``pg_stat_activity`` is cluster-wide, so one probe covers the worker's
    connections to the main and the datastore database alike.
    """
    async with await psycopg.AsyncConnection.connect(
        _libpq_url(database_url), autocommit=True
    ) as connection:
        cursor = await connection.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE client_port = ANY(%s) AND (state <> 'idle' "
            "OR backend_xid IS NOT NULL OR backend_xmin IS NOT NULL)",
            (ports,),
        )
        row = await cursor.fetchone()
    return bool(row and row[0])


async def _stop_between_transactions(worker: _Worker) -> None:
    ports = _client_ports(worker.pid)

    async def stopped() -> bool:
        os.kill(worker.pid, signal.SIGSTOP)
        # A statement the worker wrote just before it stopped may still be in
        # the socket, unread: the backend shows ``idle`` now and opens the
        # transaction a moment later, held for as long as the worker is. Look
        # once that has had time to land.
        await asyncio.sleep(_SETTLE_SECONDS)
        if not ports or not await _in_transaction(worker.database_url, ports):
            return True
        os.kill(worker.pid, signal.SIGCONT)
        return False

    await eventually(
        label=f"session worker {worker.pid} idle between transactions",
        probe=stopped,
        done=bool,
        timeout_seconds=30,
        interval_seconds=0.02,
    )


@asynccontextmanager
async def worker_held() -> AsyncIterator[None]:
    """Stop every live registered worker between transactions; resume on exit."""
    held: list[int] = []
    try:
        for worker in list(_WORKERS.values()):
            if not _alive(worker.pid):
                unregister_session_worker(worker.pid)
                continue
            # Recorded before the attempt: a failed attempt may leave it stopped.
            held.append(worker.pid)
            await _stop_between_transactions(worker)
        yield
    finally:
        for pid in held:
            if _alive(pid):
                os.kill(pid, signal.SIGCONT)
