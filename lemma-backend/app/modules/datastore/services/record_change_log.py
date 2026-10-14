"""A record write's change-log capture, and the wake after it commits.

Every record write appends its changes to its pod's change log in the
statement that writes the rows (``ChangeCapture``). A pod schema still below
the change-log version is migrated by the write's ``prepare`` on first use.
"""

from __future__ import annotations

from dataclasses import replace

from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.datastore.changes.capture import ChangeCapture
from app.modules.datastore.domain.write_intent import WriteIntent
from app.modules.datastore.services.authorization import outside_writer
from app.modules.datastore.services.table_context import TableContext


class RecordChangeLog:
    def __init__(self, *, platform_session: object | None) -> None:
        self._platform_session = platform_session

    async def capture(self, ctx: TableContext, intent: WriteIntent) -> ChangeCapture:
        """This write's appends, naming whoever outside the pod it is for."""
        writer = outside_writer(ctx)
        if writer != ctx.outside_actor:
            ctx = replace(ctx, outside_actor=writer)
        return ChangeCapture(ctx, intent=intent, outside_actor=writer)

    async def after_commit(self, changes: ChangeCapture) -> None:
        """After commit, the live wake. The platform connection is released
        across it: the write went to the datastore database, so the platform
        session has only read."""
        async with connection_released(self._platform_session):
            await changes.publish_wake()


__all__ = ["RecordChangeLog"]
