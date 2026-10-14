"""Database failures that say nothing about the work, only about the moment.

Postgres aborts a transaction to break a deadlock or a serialization conflict,
and the pool gives up when every connection is busy. In each case the same
work run again is expected to succeed, and the person whose request failed did
nothing wrong. Telling these apart from a real error is what lets a caller
retry them, and lets a failure message say "busy, try again" instead of
pointing the reader at their own settings.

Read off the driver's SQLSTATE rather than the message text: asyncpg surfaces
it, and matching on prose would break the moment a locale or a driver changes.
"""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

__all__ = [
    "DEADLOCK_DETECTED",
    "SERIALIZATION_FAILURE",
    "is_deadlock",
    "is_transient_database_error",
]

DEADLOCK_DETECTED = "40P01"
SERIALIZATION_FAILURE = "40001"

_RETRYABLE_SQLSTATES = frozenset({DEADLOCK_DETECTED, SERIALIZATION_FAILURE})

# How far down ``__cause__`` to look. A caller that wraps a database error in
# its own exception still failed for the database's reason; a short bound keeps
# a pathological chain from being walked forever.
_MAX_CAUSE_DEPTH = 4


def _sqlstate(exc: BaseException) -> str | None:
    if not isinstance(exc, DBAPIError):
        return None
    sqlstate = getattr(exc.orig, "sqlstate", None)
    return sqlstate if isinstance(sqlstate, str) else None


def _chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) <= _MAX_CAUSE_DEPTH:
        chain.append(current)
        current = current.__cause__
    return chain


def is_deadlock(exc: BaseException) -> bool:
    """Whether Postgres aborted this transaction to break a deadlock (40P01)."""
    return _sqlstate(exc) == DEADLOCK_DETECTED


def is_transient_database_error(exc: BaseException) -> bool:
    """A deadlock, a serialization failure, or a pool with no free connection.

    Also true when one of those is the direct cause of ``exc``.
    """
    return any(
        isinstance(link, PoolTimeoutError) or _sqlstate(link) in _RETRYABLE_SQLSTATES
        for link in _chain(exc)
    )
