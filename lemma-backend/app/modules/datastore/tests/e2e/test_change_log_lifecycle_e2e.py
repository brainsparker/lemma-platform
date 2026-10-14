"""The change log over time: retention, renames, batch replays, write intent,
and the cutover that converts what the old event path still held."""

from __future__ import annotations


from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import text

from app.modules.datastore.changes.reader import read_after
from app.modules.datastore.changes.retention import retain
from app.modules.datastore.changes.schema import changes_table
from app.modules.datastore.changes.subscribers import SubscriberTable
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.tests.e2e.change_log_support import (
    datastore_schedule,
    deliver_all,
    deliver_including_late,
    execute,
    log,
    orders_table,
    rewind,
    runs,
    scalar,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]


async def _age(pod_id: UUID, *, hours: int) -> None:
    async with get_datastore_engine().begin() as conn:
        await conn.execute(
            text(
                f"UPDATE {changes_table(schema_name_for_pod(pod_id))} "
                "SET at = now() - make_interval(hours => :hours)"
            ),
            {"hours": hours},
        )


async def test_retention_keeps_what_a_durable_subscriber_has_not_read(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_all()
    for n in range(10):
        await pod_api.create_record("orders", {"status": "new", "total": n})
    first_cursor = (await read_after(pod_id, None, limit=1)).changes[0].cursor
    await _age(pod_id, hours=2)

    subscribers = await SubscriberTable(pod_id).all()
    assert await retain(pod_id, subscribers) == 0, "unread changes are never deleted"
    assert len(await log(pod_id)) == 10

    await deliver_all()
    assert len(await runs(schedule.id)) == 10
    subscribers = await SubscriberTable(pod_id).all()
    assert await retain(pod_id, subscribers) == 10
    assert await log(pod_id) == []
    # A live reader whose cursor sat below what was deleted is told to resync.
    assert (await read_after(pod_id, first_cursor)).expired


async def test_retention_keeps_the_window_for_a_pod_nobody_subscribes_to(
    pod_api: DatastoreApi,
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    for n in range(3):
        await pod_api.create_record("orders", {"status": "old", "total": n})
    await _age(pod_id, hours=2)
    await pod_api.create_record("orders", {"status": "recent", "total": 9})

    assert await retain(pod_id, []) == 3
    [kept] = await log(pod_id)
    assert kept.at > datetime.now(timezone.utc) - timedelta(minutes=5)


async def test_a_renamed_table_keeps_its_subscriber(
    pod_api: DatastoreApi, fixed_test_user
):
    """Subscribers and changes name a table by id, not by name."""
    pod_id = UUID(pod_api.pod_id)
    table_id = await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_all()
    schema = schema_name_for_pod(pod_id)
    async with get_datastore_engine().begin() as conn:
        await conn.execute(text(f'ALTER TABLE "{schema}".orders RENAME TO purchases'))
    await execute(
        "UPDATE datastore_tables SET table_name = 'purchases' WHERE id = :id",
        id=str(table_id),
    )

    await pod_api.create_record("purchases", {"status": "new", "total": 1})
    await deliver_all()
    [change] = await log(pod_id)
    assert change.table_id == table_id
    assert [run["source_event_id"] for run in await runs(schedule.id)] == [
        f"chg:{change.position}"
    ]


async def test_a_bulk_write_may_ask_to_fire_like_single_writes(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_all()
    response = await pod_api.request(
        "POST",
        f"/pods/{pod_id}/datastore/tables/orders/records/bulk/create",
        json={
            "records": [{"status": "new", "total": n} for n in range(4)],
            "intent": "normal",
        },
    )
    assert response.status_code == 200, response.text
    await deliver_all()
    assert len(await runs(schedule.id)) == 4


async def test_a_batch_trigger_never_fires_a_window_twice_on_replay(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"], bulk_writes="batch"
    )
    await deliver_including_late()
    await pod_api.bulk_create(
        "orders", [{"status": "new", "total": n} for n in range(30)]
    )
    await deliver_including_late()  # the window delay, pulled forward
    [first] = await runs(schedule.id)
    assert first["source_event_id"].startswith(f"batch:{schedule.id}:")

    # Replayed after a crash, with more changes now readable after the window:
    # the planned window is not fired again; what follows is its own batch.
    await pod_api.bulk_create(
        "orders", [{"status": "new", "total": n} for n in range(5)]
    )
    await rewind(pod_id, f"schedule:{schedule.id}")
    await deliver_including_late()
    fired = await runs(schedule.id)
    assert len(fired) == 2
    assert fired[0] == first
    assert (
        await scalar(
            "SELECT sum(count) FROM trigger_batches WHERE schedule_id = :id",
            id=str(schedule.id),
        )
        == 35
    )
