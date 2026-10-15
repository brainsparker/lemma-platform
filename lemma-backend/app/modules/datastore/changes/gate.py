"""Which pod schemas a record write could not cheaply bring to the change log.

A write appends to its pod's ``reserved_changes``, which exists only in schemas
at pod-schema version 3, and a writer migrates its own schema there on first
use (``ChangeCapture.prepare``). That is cheap for a schema that holds no search
chunks -- a new pod's, or one nothing has indexed -- and not for one whose
chunks the version-2 step rewrites, which is the background backfill's work.

Record writes always append to the log now. The release before that switched
per process once nothing was blocking; this count is what the cutover check
(``app/cutover_check.py``) reads to say the backfill had finished before the
outbox path was removed.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.datastore.infrastructure.pod_schema_migrations import (
    CHANGE_LOG_VERSION,
    VERSIONS_TABLE,
)
from app.modules.datastore.infrastructure.query_role import POD_SCHEMA_RE


async def schemas_blocking_switch(engine: AsyncEngine) -> int:
    """Pod schemas a record write could not cheaply bring to the change log."""
    async with engine.connect() as conn:
        if not await conn.scalar(
            text(f"SELECT to_regclass('{VERSIONS_TABLE}') IS NOT NULL")
        ):
            versions = "SELECT NULL::text AS schema_name, 0 AS version WHERE false"
        else:
            versions = f"SELECT schema_name, version FROM {VERSIONS_TABLE}"
        count = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_namespace n "
                f"LEFT JOIN ({versions}) v ON v.schema_name = n.nspname "
                "WHERE n.nspname ~ :pattern AND ("
                "(v.version IS NOT NULL AND v.version < :version) OR "
                "(v.version IS NULL AND to_regclass("
                "quote_ident(n.nspname) || '.reserved_chunks') IS NOT NULL))"
            ),
            {"pattern": f"^{POD_SCHEMA_RE.pattern}$", "version": CHANGE_LOG_VERSION},
        )
    return int(count or 0)


__all__ = ["schemas_blocking_switch"]
