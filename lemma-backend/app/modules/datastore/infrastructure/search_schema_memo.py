"""Which pod schemas this process has already set up for search.

``PostgresSearchService.ensure_schema`` is idempotent DDL behind a global
advisory lock, and a service is built per call, so a flag on the instance
remembered nothing: every search, index and remove ran the whole setup again.
``CREATE INDEX IF NOT EXISTS`` takes its table lock before it checks whether
the index exists, so each of those queued behind any in-flight chunk write in
that pod -- while holding the lock every other pod's setup needs.

This remembers the answer for the life of the process. It is not a data cache
and holds nothing another replica could disagree about: a schema that exists
keeps existing until something here drops it, and the two places that drop
one call ``forget``. Another process dropping a schema leaves this one's memo
stale for a pod that has just been deleted, whose searches are refused before
they get here.
"""

from __future__ import annotations

from app.core.bounded import BoundedSet

# Bounded like every process-lifetime memo keyed by something that grows: an
# evicted schema costs one more run of idempotent DDL, never a wrong answer.
# There is one pod-data database per process, so the schema name is the key.
_ensured: BoundedSet[str] = BoundedSet(8192, name="datastore.search_schemas_ensured")


def is_ensured(schema_name: str) -> bool:
    return schema_name in _ensured


def mark_ensured(schema_name: str) -> None:
    _ensured.add(schema_name)


def forget(schema_name: str) -> None:
    _ensured.discard(schema_name)
