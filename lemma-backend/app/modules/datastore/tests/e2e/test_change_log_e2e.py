"""The record change log, end to end: API writes, the log, DATASTORE triggers.

Real Postgres for both databases, the real record API, and the real work
runtime running the ``changes.*`` kinds in process.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.modules.datastore.changes.reader import ChangeOp, read_after
from app.modules.datastore.changes.schema import PENDING_TABLE
from app.modules.datastore.tests.e2e.change_log_support import (
    change_log_mode,
    datastore_scalar,
    datastore_schedule,
    deliver_all,
    log,
    orders_table,
    rewind,
    runs,
    scalar,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _change_log_on():
    with change_log_mode("on"):
        yield


async def test_unwatched_writes_append_slim_rows_and_wake_nobody(pod_api: DatastoreApi):
    pod_id = UUID(pod_api.pod_id)
    table_id = await orders_table(pod_api)

    created = await pod_api.create_record("orders", {"status": "new", "total": 1})
    await pod_api.update_record("orders", created["id"], {"status": "paid"})
    await pod_api.bulk_create(
        "orders", [{"status": "new", "total": n} for n in range(3)]
    )
    await pod_api.delete_record("orders", created["id"])

    changes = await log(pod_id)
    assert [change.op for change in changes] == [
        ChangeOp.INSERT,
        ChangeOp.UPDATE,
        ChangeOp.INSERT,
        ChangeOp.INSERT,
        ChangeOp.INSERT,
        ChangeOp.DELETE,
    ]
    assert {change.table_id for change in changes} == {table_id}
    assert [change.intent for change in changes] == [
        "normal",
        "normal",
        "bulk",
        "bulk",
        "bulk",
        "normal",
    ]
    assert changes[0].record_id == created["id"] == changes[-1].record_id
    # Nothing watches the table: no snapshot, no wake row.
    assert all(c.new_row is None and c.old_values is None for c in changes)
    assert (
        await datastore_scalar(
            f"SELECT count(*) FROM {PENDING_TABLE} WHERE pod_id = CAST(:pod AS uuid)",
            pod=str(pod_id),
        )
        == 0
    )
    # A cursor after the last change reads nothing, and is stable.
    page = await read_after(pod_id, changes[-1].cursor)
    assert page.changes == [] and not page.full


async def test_a_watched_table_logs_snapshots_and_each_change_fires_once(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id,
        UUID(fixed_test_user["id"]),
        operations=["INSERT", "UPDATE"],
        when={"status": {"equals": "paid"}},
    )
    await deliver_all()  # the sync: the subscriber row now exists

    paid = await pod_api.create_record("orders", {"status": "paid", "total": 5})
    pending = await pod_api.create_record("orders", {"status": "new", "total": 7})
    await pod_api.update_record("orders", pending["id"], {"status": "paid"})
    await pod_api.delete_record("orders", paid["id"])  # not watched: slim

    changes = await log(pod_id)
    insert, _, update, delete = changes
    assert insert.new_row is not None and insert.new_row["status"] == "paid"
    assert update.old_values == {"status": "new"}
    assert update.new_row is not None and update.new_row["status"] == "paid"
    assert delete.new_row is None and delete.old_values is None

    await deliver_all()
    fired = [run["source_event_id"] for run in await runs(schedule.id)]
    assert fired == [f"chg:{insert.position}", f"chg:{update.position}"]

    # Delivered again from the start (a crash before the cursor moved): the
    # run keys absorb it.
    await rewind(pod_id, f"schedule:{schedule.id}")
    await deliver_all()
    assert [run["source_event_id"] for run in await runs(schedule.id)] == fired


async def test_bulk_writes_fire_nothing_by_default_and_are_recorded_as_skipped(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_all()

    for offset in range(0, 1200, 400):
        await pod_api.bulk_create(
            "orders",
            [{"status": "new", "total": n} for n in range(offset, offset + 400)],
        )
    await deliver_all()

    assert await runs(schedule.id) == []
    assert (
        await scalar(
            "SELECT sum(count) FROM trigger_skips WHERE schedule_id = :id "
            "AND reason = 'bulk_write'",
            id=str(schedule.id),
        )
        == 1200
    )
    # Aggregated: one row per page of changes, not one per change.
    assert (
        await scalar(
            "SELECT count(*) FROM trigger_skips WHERE schedule_id = :id",
            id=str(schedule.id),
        )
        <= 3
    )
