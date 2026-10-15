"""The cutover from the old transport, end to end: every owed delivery takes effect once.

A database is left as the old transport left it at deploy time -- an outbox
(and, on a separate datastore database, that database's outbox) holding an
owed event of every type that had a consumer, beside deliveries the old
consumers had already settled -- and streaq jobs in Redis whose work existed
only there. Then the release's cutover runs, as the worker runs it:

* the sweep takes over what is owed, and each ``cutover.legacy_event`` writes
  what today's code writes for that event: a record change lands in its pod's
  change log and fires its trigger once; a schedule fire starts its target once;
  a finished target's outcome is recorded; every other event becomes the work
  item (or fact) that replaced its consumer, once;
* delivering the same events again changes nothing;
* once the previous release is gone (quiet), the sweep converts the streaq
  jobs only Redis held, runs the state routes and drops the old tables --
  the main ones and the datastore database's outbox -- and finishes.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid7

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.core.infrastructure.cutover import routes as cutover_routes
from app.core.infrastructure.cutover.routes import retire_streaq_jobs
from app.core.infrastructure.cutover.takeover import LEGACY_EVENT, LEGACY_SWEEP
from app.core.infrastructure.cutover.work import (
    LegacyEvent,
    SweepState,
    register_cutover_work,
)
from app.core.infrastructure.db.session import async_session_maker, get_engine
from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    WorkRegistry,
    WorkWriter,
)
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.events import (
    DatastoreRecordEvent,
    DatastoreRecordOperation,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.tests.e2e.change_log_support import (
    datastore_schedule,
    deliver_all,
    handoff,
    log,
    log_final,
    orders_table,
    runs,
    scalar,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.test_support.e2e.legacy_transport import (
    create_legacy_tables,
    event,
    inbox_row,
    outbox_row,
    streaq_job,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]

# Every module's converters, as the worker registers them.
import app.core.infrastructure.cutover.work  # noqa: E402,F401
import app.modules.agent.events.legacy_cutover  # noqa: E402,F401
import app.modules.agent_surfaces.events.legacy_cutover  # noqa: E402,F401
import app.modules.analytics.events.legacy_cutover  # noqa: E402,F401
import app.modules.datastore.changes.legacy_cutover  # noqa: E402,F401
import app.modules.identity.events.legacy_cutover  # noqa: E402,F401
import app.modules.mcp_access.events.legacy_cutover  # noqa: E402,F401
import app.modules.pod.events.legacy_cutover  # noqa: E402,F401
import app.modules.schedule.events.legacy_cutover  # noqa: E402,F401
import app.modules.workflow.events.legacy_cutover  # noqa: E402,F401

#: The analytics events the old ``analytics.*`` consumers projected.
_ANALYTICS = {
    "agent.created": {"agent_id": uuid4()},
    "agent.conversation.started": {"conversation_id": uuid4(), "user_id": uuid4()},
    "app.created": {"app_id": uuid4()},
    "app.published": {"app_id": uuid4()},
    "connector.connected": {"connector_id": "github", "organization_id": uuid4()},
    "datastore.table.created": {"table_id": uuid4(), "table_name": "t"},
    "datastore.file.created": {"file_id": uuid4()},
    "function.created": {"function_id": uuid4()},
    "identity.organization.created": {"organization_id": uuid4()},
    "identity.organization.member_added": {
        "organization_id": uuid4(),
        "role": "MEMBER",
    },
    "pod.member.added": {"role": "MEMBER", "email": "a@b.c"},
    "pod_bundle.exported": {"bundle_id": uuid4()},
    "pod_bundle.import_completed": {"bundle_id": uuid4()},
    "schedule.created": {"schedule_type": "TIME", "config": {}},
    "schedule.run.completed": {"schedule_type": "TIME", "status": "COMPLETED"},
    "surface.connected": {"surface_id": uuid4(), "platform": "TELEGRAM"},
    "workflow.created": {"workflow_id": uuid4()},
}


def _cutover_registry() -> WorkRegistry:
    registry = WorkRegistry()
    register_cutover_work(registry)
    return registry


async def _items(kind: str, **match: str) -> int:
    where = " AND ".join(f"payload->>'{k}' = :{k}" for k in match)
    return int(
        await scalar(
            f"SELECT count(*) FROM work_items WHERE kind = :kind"
            f"{' AND ' + where if where else ''}",
            kind=kind,
            **match,
        )
        or 0
    )


async def _dedup(kind: str, key: str) -> int:
    return int(
        await scalar(
            "SELECT count(*) FROM work_items WHERE kind = :kind AND dedup_key = :key",
            kind=kind,
            key=key,
        )
        or 0
    )


async def _run_cutover() -> int:
    # Each converter holds its session while its writer may open a side
    # connection for a new namespace's queue row: stay inside the test pool.
    return await run_until_idle(
        async_session_maker, registry=_cutover_registry(), slots=4
    )


async def _deliver_changes() -> None:
    """Hand off and deliver change-log work only: the items the cutover wrote
    for other routes stay queued, to be counted."""
    from app.modules.datastore.changes.work import register_change_work

    registry = WorkRegistry()
    register_change_work(registry)
    await log_final()
    while True:
        await handoff()
        if not await run_until_idle(async_session_maker, registry=registry):
            return


async def test_every_owed_delivery_takes_effect_once(
    pod_api: DatastoreApi, fixed_test_user, fixed_test_org, monkeypatch
) -> None:
    from app.core.analytics import domain_events

    projected: Counter[str] = Counter()

    #: The owed events' ids: the in-process sink also projects whatever this
    #: session's other tests committed, which is not the cutover's doing.
    owed_ids: set[str] = set()

    async def capture(payload: dict[str, Any]) -> None:
        if str(payload.get("event_id")) in owed_ids:
            projected[str(payload["event_type"])] += 1

    monkeypatch.setattr(
        domain_events,
        "_projections",
        {
            event_type: [capture]
            for event_type in (
                *_ANALYTICS,
                "pod.created",
                "pod.deleted",
                "agent.run.completed",
                "identity.user.signed_up",
                "workflow.run.terminal",
            )
        },
    )

    pod_id = UUID(pod_api.pod_id)
    user_id = UUID(fixed_test_user["id"])
    org_id = UUID(fixed_test_org["id"])
    await orders_table(pod_api)
    schedule = await datastore_schedule(pod_id, user_id, operations=["INSERT"])
    await deliver_all()  # the trigger's change-log subscriber exists

    # Domain state the outcome routes settle against.
    conversation_id, workflow_run_id = uuid4(), uuid4()
    agent_run_id = uuid4()
    stale = datetime.now(UTC) - timedelta(minutes=30)
    async with async_session_maker() as session, session.begin():
        session.add(
            ConversationModel(
                id=conversation_id, user_id=user_id, pod_id=pod_id, status="COMPLETED"
            )
        )
        await session.flush()
        for kind, target in (("AGENT", conversation_id), ("WORKFLOW", workflow_run_id)):
            session.add(
                ScheduleRun(
                    id=uuid7(),
                    schedule_id=schedule.id,
                    user_id=user_id,
                    source_event_id=f"dispatched-{kind.lower()}",
                    status="DISPATCHED",
                    attempts=1,
                    target_kind=kind,
                    target_run_id=str(target),
                    payload={},
                    fire_metadata={},
                    llm_output={},
                    started_at=stale,
                    created_at=stale,
                    updated_at=stale,
                )
            )

    separate = (
        bool(datastore_settings.datastore_database_url)
        and datastore_settings.datastore_database_url != settings.database_url
    )
    record = DatastoreRecordEvent.create(
        pod_id=pod_id,
        table_name="orders",
        record_id=str(uuid4()),
        operation=DatastoreRecordOperation.INSERT,
        payload={"status": "legacy", "total": 1},
        owner_user_id=user_id,
    ).model_dump(mode="json")
    settled_record = DatastoreRecordEvent.create(
        pod_id=pod_id,
        table_name="orders",
        record_id=str(uuid4()),
        operation=DatastoreRecordOperation.INSERT,
        payload={"status": "already fired", "total": 2},
        owner_user_id=user_id,
    ).model_dump(mode="json")

    other_pod = uuid4()
    join_request_id = uuid4()
    owed = {
        "join": event(
            "pod.join_request.requested",
            pod_id=other_pod,
            organization_id=org_id,
            requester_user_id=user_id,
            join_request_id=join_request_id,
        ),
        "created": event(
            "pod.created",
            pod_id=other_pod,
            organization_id=org_id,
            creator_id=user_id,
            name="p",
        ),
        "deleted": event(
            "pod.deleted",
            pod_id=other_pod,
            organization_id=org_id,
            deleted_by_user_id=user_id,
        ),
        "removed": event("pod.member.removed", pod_id=other_pod, user_id=user_id),
        "paused": event(
            "schedule.deactivated",
            schedule_id=schedule.id,
            schedule_type="DATASTORE",
            user_id=user_id,
            pod_id=pod_id,
            consecutive_failures=5,
            reason="consecutive_failures",
        ),
        "fired": event(
            "schedule.fired",
            schedule_id=schedule.id,
            schedule_type="DATASTORE",
            user_id=user_id,
            payload={"from": "legacy"},
            source_event_id="legacy-fire-1",
        ),
        "terminal": event(
            "workflow.run.terminal",
            run_id=workflow_run_id,
            status="COMPLETED",
            completed_at=datetime.now(UTC).isoformat(),
        ),
        "completed": event(
            "agent.run.completed",
            conversation_id=conversation_id,
            agent_run_id=agent_run_id,
            status="COMPLETED",
        ),
        "settled": event(
            "notification.settled", conversation_id=conversation_id, pod_id=pod_id
        ),
        "signed_up": event("identity.user.signed_up", user_id=user_id, email="a@b.c"),
        "phone": event(
            "identity.user.phone.replaced",
            user_id=user_id,
            email="a@b.c",
            mobile_number="+15550000",
        ),
        "invited": event(
            "identity.organization.invitation.created",
            invitation_id=uuid4(),
            organization_id=org_id,
            organization_name="o",
            invited_email="x@y.z",
            role="MEMBER",
            invited_by_user_id=user_id,
            invited_by_email="a@b.c",
            accept_url="https://example.invalid/accept",
        ),
        "accepted": event(
            "identity.organization.invitation.accepted",
            invitation_id=uuid4(),
            organization_id=org_id,
            organization_name="o",
            accepted_user_id=user_id,
            accepted_email="x@y.z",
            role="MEMBER",
        ),
        "whatsapp": event(
            "identity.mobile_verification.whatsapp.received",
            code="123456",
            sender_wa_id="15550000",
            destination_phone_number_id="pn",
            whatsapp_message_id="wamid.legacy",
        ),
        "mobile": event("identity.user.mobile.changed", user_id=user_id),
        "onboarding": event("surface.onboarding.ready", pending_id=uuid4()),
    }
    for event_type, fields in _ANALYTICS.items():
        owed[event_type] = event(event_type, pod_id=pod_id, **fields)
    owed_ids.update(str(payload["event_id"]) for payload in owed.values())

    engine = get_engine()
    async with engine.begin() as conn:
        await create_legacy_tables(conn)
        for payload in owed.values():
            await outbox_row(conn, payload, published=True)
        # Settled by its consumer already: not owed again.
        done = await outbox_row(
            conn,
            event(
                "pod.join_request.requested",
                pod_id=other_pod,
                organization_id=org_id,
                requester_user_id=user_id,
                join_request_id=uuid4(),
            ),
            published=True,
        )
        await inbox_row(
            conn, "pod.join-request-email", done, "pod.join_request.requested"
        )
        if not separate:
            await outbox_row(conn, record)
            settled_id = await outbox_row(conn, settled_record, published=True)
            await inbox_row(
                conn,
                "schedule-datastore-events",
                settled_id,
                "datastore.record.insert",
            )
    if separate:
        async with get_datastore_engine().begin() as conn:
            await create_legacy_tables(conn, inbox=False)
            await outbox_row(conn, record)
            settled_id = await outbox_row(conn, settled_record, published=True)
        async with engine.begin() as conn:
            await create_legacy_tables(conn)
            await inbox_row(
                conn,
                "schedule-datastore-events",
                settled_id,
                "datastore.record.insert",
            )

    # The migration queued the sweep; a long quiet window keeps the tables.
    async with async_session_maker() as session:
        await session.execute(
            text("DELETE FROM work_items WHERE kind = :k"), {"k": LEGACY_SWEEP}
        )
        await WorkWriter(session).add(
            LEGACY_SWEEP,
            SYSTEM_NAMESPACE,
            SweepState(quiet_seconds=3600, poll_seconds=3600),
            dedup_key=LEGACY_SWEEP,
        )
        await session.commit()
    await _run_cutover()
    assert (
        await scalar(
            "SELECT count(*) FROM work_items WHERE kind = :k AND state = 'dead'",
            k=LEGACY_EVENT,
        )
        == 0
    ), await scalar(
        "SELECT string_agg(payload->>'route' || ': ' || coalesce(last_error, ''), ' | ') "
        "FROM work_items WHERE kind = :k AND state = 'dead'",
        k=LEGACY_EVENT,
    )

    async def effects() -> dict[str, object]:
        return {
            "join": await _dedup(
                "pod.join_request.email", f"pod-join-request:{join_request_id}"
            ),
            "provision": await _items("pod.provision", pod_id=str(other_pod)),
            "schedule.pod_deleted": await _items(
                "schedule.pod_deleted", pod_id=str(other_pod)
            ),
            "surfaces.pod_deleted": await _items(
                "surfaces.pod_deleted", pod_id=str(other_pod)
            ),
            "agent.pod_links": await _items(
                "agent.pod_links.pod_deleted", pod_id=str(other_pod)
            ),
            "mcp_access": await _items("mcp_access.pod_deleted", pod_id=str(other_pod)),
            "member_removed": await _items(
                "surfaces.member_removed", pod_id=str(other_pod)
            ),
            "paused": await _dedup(
                "schedule.paused_notice",
                f"schedule-paused:{schedule.id}:consecutive_failures",
            ),
            "fired": sum(
                1
                for run in await runs(schedule.id)
                if run["source_event_id"] == "legacy-fire-1"
            ),
            "outcomes": await scalar(
                "SELECT string_agg(target_kind || '=' || coalesce(target_outcome, '-'), ',' "
                "ORDER BY target_kind) FROM schedule_runs WHERE schedule_id = :s "
                "AND source_event_id LIKE 'dispatched-%'",
                s=schedule.id,
            ),
            "followup": await _items(
                "agent.followup", conversation_id=str(conversation_id)
            ),
            "title": await _items("agent.title", conversation_id=str(conversation_id)),
            "replies": await _items(
                "agent.replies_arrived", conversation_id=str(conversation_id)
            ),
            "emails": sum(
                [
                    await _dedup(
                        "identity.email", f"identity-email:{owed[key]['event_id']}"
                    )
                    for key in ("signed_up", "phone", "invited", "accepted")
                ]
            ),
            "whatsapp": await _dedup(
                "identity.whatsapp_verification", "whatsapp-verification:wamid.legacy"
            ),
            "mobile": await _items("surfaces.mobile_changed", user_id=str(user_id)),
            "analytics": dict(projected),
        }

    first = await effects()
    assert first == {
        "join": 1,
        "provision": 1,
        "schedule.pod_deleted": 1,
        "surfaces.pod_deleted": 1,
        "agent.pod_links": 1,
        "mcp_access": 1,
        "member_removed": 1,
        "paused": 1,
        "fired": 1,
        "outcomes": "AGENT=COMPLETED,WORKFLOW=COMPLETED",
        "followup": 1,
        "title": 1,
        "replies": 1,
        "emails": 4,
        "whatsapp": 1,
        "mobile": 1,
        "analytics": {
            **dict.fromkeys(_ANALYTICS, 1),
            "pod.created": 1,
            "pod.deleted": 1,
            "agent.run.completed": 1,
            "identity.user.signed_up": 1,
            "workflow.run.terminal": 1,
        },
    }

    # The owed record change: one row in the log under its event id, and the
    # trigger fires it once; the settled one is not appended again.
    await _deliver_changes()
    converted = [c for c in await log(pod_id) if c.event_id is not None]
    assert [str(c.event_id) for c in converted] == [record["event_id"]]
    fired = [
        r for r in await runs(schedule.id) if r["source_event_id"] == record["event_id"]
    ]
    assert len(fired) == 1

    # Every owed delivery again (an old worker finishing it too, a redelivery):
    # nothing takes effect a second time.
    async with async_session_maker() as session:
        work = WorkWriter(session)
        for route, payload in [
            ("schedule-datastore-events", record),
            ("mcp-access.record-created", record),
            ("workflow.schedule-start", owed["fired"]),
            ("schedule.agent-outcomes", owed["completed"]),
            ("schedule.workflow-outcomes", owed["terminal"]),
            ("pod.join-request-email", owed["join"]),
            ("identity-email-events", owed["signed_up"]),
            ("identity-mobile-verification-events", owed["whatsapp"]),
            ("schedule-notifications", owed["paused"]),
        ]:
            await work.add(
                LEGACY_EVENT, SYSTEM_NAMESPACE, LegacyEvent(route=route, event=payload)
            )
        await session.commit()
    projected.clear()
    await _run_cutover()
    await _deliver_changes()
    again = await effects()
    assert {k: v for k, v in again.items() if k != "analytics"} == {
        k: v for k, v in first.items() if k != "analytics"
    }
    assert [str(c.event_id) for c in await log(pod_id) if c.event_id is not None] == [
        record["event_id"]
    ]
    assert (
        len(
            [
                r
                for r in await runs(schedule.id)
                if r["source_event_id"] == record["event_id"]
            ]
        )
        == 1
    )

    # The previous release is gone: streaq leftovers, then the drop.
    redis = get_redis(decode_responses=False)
    jobs = {
        b"streaq:default:task:data:e2e-title": streaq_job(
            "generate_conversation_title",
            context={"conversation_id": str(conversation_id)},
        ),
        b"streaq:default:task:data:e2e-fire": streaq_job(
            "check_and_start_flows_for_schedule",
            schedule_id=str(schedule.id),
            user_id=str(user_id),
            payload={},
            schedule_event_id="legacy-job-1",
        ),
        b"streaq:bulk:task:data:e2e-cleanup": streaq_job(
            "cleanup_deleted_datastore_paths",
            pod_id=str(pod_id),
            is_folder=False,
            folder_prefix=None,
            files=[],
        ),
        b"streaq:default:task:data:e2e-seeded": streaq_job(
            "process_agent_run", run_id=str(agent_run_id)
        ),
        b"streaq:default:task:data:e2e-unknown": streaq_job("some_cron"),
        # A deployment's own retired job (lemma-cloud's), registered by name.
        b"streaq:default:task:data:e2e-retired": streaq_job("e2e_retired_cron"),
    }
    for key, value in jobs.items():
        await redis.set(key, value)
    retire_streaq_jobs("e2e_retired_cron")
    try:
        async with async_session_maker() as session:
            await session.execute(
                text(
                    "UPDATE work_items SET payload = payload || "
                    "'{\"quiet_seconds\": 0}'::jsonb WHERE kind = :k"
                ),
                {"k": LEGACY_SWEEP},
            )
            await session.commit()
        await make_due(async_session_maker, kind=LEGACY_SWEEP)
        await _run_cutover()

        assert (
            await scalar(
                "SELECT count(*) FROM work_items WHERE kind IN (:a, :b)",
                a=LEGACY_SWEEP,
                b=LEGACY_EVENT,
            )
            == 0
        ), "the sweep finished and every converted delivery ran"
        assert await scalar(
            "SELECT to_regclass('domain_event_outbox') IS NULL "
            "AND to_regclass('domain_event_inbox') IS NULL"
        )
        if separate:
            async with get_datastore_engine().connect() as conn:
                assert await conn.scalar(
                    text("SELECT to_regclass('domain_event_outbox') IS NULL")
                )
        assert [k for k in jobs if await redis.exists(k)] == [
            b"streaq:default:task:data:e2e-unknown"
        ], "converted, seeded and registered jobs are gone; an unknown one is left"
        assert await _items("agent.title", conversation_id=str(conversation_id)) == 1
        assert (
            sum(
                1
                for r in await runs(schedule.id)
                if r["source_event_id"] == "legacy-job-1"
            )
            == 1
        )
        assert (
            await scalar(
                "SELECT count(*) FROM work_items WHERE kind = 'file.cleanup' AND ns = :ns",
                ns=f"pod:{pod_id}",
            )
            == 1
        )
    finally:
        await redis.delete(*jobs)
        cutover_routes._retired_streaq.discard("e2e_retired_cron")  # noqa: SLF001
