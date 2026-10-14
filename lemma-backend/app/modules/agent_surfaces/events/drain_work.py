"""``surfaces.drain_events``: the cutover converter for what the old transport still holds.

Run once after the deploy that moves surfaces onto the work runtime (migration
0060 seeds the item), and exits when there is nothing left:

* **Unread ``surface_events`` entries** -- read with plain ``XRANGE``, no
  FastStream: for each old consumer group, the entries past its last-delivered
  id plus the ones it had delivered but never acknowledged.
  ``surface.webhook.received`` becomes a ``surface_inbound_messages`` row and
  its ``surface.ingest`` (the row's unique key makes a re-run harmless);
  ``surface.onboarding.ready`` becomes the onboarding's ingest item;
  ``notification.settled`` becomes the ``NotificationSettled`` fact.
  Entries the old consumer already finished are *not* converted: they were
  answered, and the durable dedup that would stop a second answer is new.
* **Unpublished outbox rows** of those types -- converted the same way (the
  relay may still publish them; the targets are idempotent).
* **Owed replies** -- ``surface_owed_reply`` records in conversation metadata
  (the #961 debt the owed-reply job and its sweep paid) become PENDING
  ``surface_deliveries`` rows due when the record was, and are cleared.
* **Open notifications without an expiry item** -- given one.

Progress per group is kept in ``surface_receiver_state`` (``drain:<group>``),
so a crash resumes where it stopped. While anything is left it defers itself
10 s and looks again; it completes when every group is caught up and the
outbox holds none of these types, destroying the three groups so the cutover
check (``app/cutover_check.py``) reads them as drained. Never deletes a stream
entry: dropping what is left of the transport is migration 0063's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import func, select

from app.core.infrastructure.cutover.legacy_streams import decode_entry, destroy_group
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.cutover.legacy_tables import DomainEventOutbox
from app.core.infrastructure.redis.client import get_redis
from app.core.infrastructure.work import (
    Defer,
    JobContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
    facts,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.contracts.notification_facts import (
    NotificationSettled,
)
from app.modules.agent_surfaces.domain.deliveries import DeliveryContent
from app.modules.agent_surfaces.domain.inbound_messages import (
    SHARED_RECEIVER_SCOPE,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.work_payloads import NoPayload
from app.modules.agent_surfaces.events.work_kinds import SURFACE_DRAIN_EVENTS
from app.modules.agent_surfaces.infrastructure.repositories.inbound_message_repository import (  # noqa: E501
    ReceiverStateRepository,
)
from app.modules.agent_surfaces.services.inbound_acceptor import (
    InboundAcceptor,
    InboundDelivery,
)
from app.modules.agent_surfaces.services.onboarding_handoff import (
    hand_off_when_ready,
)
from app.modules.agent_surfaces.services.surface_deliveries import (
    run_reply_idempotency_key,
    stage_conversation_message,
)

logger = get_logger(__name__)

STREAM = "surface_events"
WEBHOOK = "surface.webhook.received"
ONBOARDING_READY = "surface.onboarding.ready"
SETTLED = "notification.settled"
#: The old consumer groups, and the event types each one owned.
GROUPS: dict[str, frozenset[str]] = {
    "surface-webhook-events": frozenset({WEBHOOK}),
    "agent-surfaces.onboarding": frozenset({ONBOARDING_READY}),
    "agent-notification-settled": frozenset({SETTLED}),
}
OWED_REPLY_KEY = "surface_owed_reply"
_BATCH = 200
_POLL_AGAIN = timedelta(seconds=10)
_NOTIFICATIONS_ARMED = "drain:notification-expiry"
_STARTED = "drain:started"
#: How long the drain keeps looking after it first ran. A rolling deploy keeps
#: old API pods publishing webhooks to the stream for a while after the first
#: new worker starts; exiting the moment the stream looked empty would strand
#: whatever they published next.
MIN_DRAIN = timedelta(minutes=30)


@dataclass
class DrainReport:
    converted: dict[str, int] = field(default_factory=dict)
    left: int = 0

    def count(self, what: str, n: int = 1) -> None:
        self.converted[what] = self.converted.get(what, 0) + n


def _id_after(entry_id: str) -> str:
    milliseconds, _, sequence = entry_id.partition("-")
    return f"{milliseconds}-{int(sequence or 0) + 1}"


class SurfaceEventDrain:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, redis: Any | None = None
    ) -> None:
        self.uow_factory = uow_factory
        self.redis = redis

    def _client(self) -> Any:
        # Raw bytes: FastStream's binary envelope is not UTF-8.
        return self.redis or get_redis(decode_responses=False)

    async def convert(self, event: dict[str, Any], report: DrainReport) -> None:
        kind = event.get("event_type")
        if kind == WEBHOOK:
            await self._webhook(event, report)
        elif kind == ONBOARDING_READY and event.get("pending_id"):
            async with self.uow_factory() as uow:
                await hand_off_when_ready(uow, UUID(str(event["pending_id"])))
            report.count("onboarding")
        elif kind == SETTLED and event.get("conversation_id") and event.get("pod_id"):
            async with self.uow_factory() as uow:
                await facts.emit(
                    uow,
                    NotificationSettled(
                        conversation_id=UUID(str(event["conversation_id"])),
                        pod_id=UUID(str(event["pod_id"])),
                    ),
                )
            report.count("settled")

    async def _webhook(self, event: dict[str, Any], report: DrainReport) -> None:
        source = str(event.get("source") or "")
        payload = event.get("payload") or {}
        headers = dict(event.get("headers") or {})
        surface_id = event.get("surface_id")
        receivers = event.get("receiver_surface_ids")
        source_event_id = str(
            event.get("source_event_id") or event.get("event_id") or ""
        )
        if not source or not isinstance(payload, dict) or not source_event_id:
            report.count("skipped")
            return
        if surface_id:
            request: SurfaceDirectWebhookIngress | SurfacePlatformWebhookIngress = (
                SurfaceDirectWebhookIngress(
                    surface_id=UUID(str(surface_id)), payload=payload, headers=headers
                )
            )
            scope = str(surface_id)
        else:
            request = SurfacePlatformWebhookIngress(
                source=source,
                payload=payload,
                headers=headers,
                receiver_surface_ids=(
                    [UUID(str(item)) for item in receivers] if receivers else None
                ),
            )
            scope = headers.get("x-lemma-surface-receiver-key") or SHARED_RECEIVER_SCOPE
        result = await InboundAcceptor(self.uow_factory).accept(
            InboundDelivery(
                request=request,
                receiver_scope=scope,
                source_event_id=source_event_id,
            )
        )
        report.count(f"webhook_{result.outcome}")

    async def drain_group(self, group: str, report: DrainReport) -> int:
        """Convert what ``group`` had not finished; how many entries are left."""
        client = self._client()
        types = GROUPS[group]
        state_key = f"drain:{group}"
        async with self.uow_factory() as uow:
            cursor = await ReceiverStateRepository(uow.session).load(state_key)
        if cursor is None:
            groups = {
                _text(_field(info, "name")): info
                for info in await client.xinfo_groups(STREAM)
            }
            info = groups.get(group)
            if info is None:
                await self._store(state_key, "done")
                return 0
            pending = await client.xpending_range(
                STREAM, group, min="-", max="+", count=10_000
            )
            for item in pending:
                entry_id = _text(_field(item, "message_id"))
                for _, fields in await client.xrange(STREAM, entry_id, entry_id):
                    event = decode_entry(fields)
                    if event and event.get("event_type") in types:
                        await self.convert(event, report)
            cursor = _id_after(_text(_field(info, "last-delivered-id")) or "0-0")
            await self._store(state_key, cursor)
        if cursor == "done":
            return 0
        entries = await client.xrange(STREAM, min=cursor, max="+", count=_BATCH)
        for entry_id, fields in entries:
            event = decode_entry(fields)
            if event and event.get("event_type") in types:
                await self.convert(event, report)
            cursor = _id_after(_text(entry_id))
        await self._store(state_key, cursor)
        return len(entries)

    async def _store(self, key: str, value: str) -> None:
        async with self.uow_factory() as uow:
            await ReceiverStateRepository(uow.session).store(key, value)

    async def drain_outbox(self, report: DrainReport) -> int:
        """Convert unpublished outbox rows of these types; how many remain."""
        types = [WEBHOOK, ONBOARDING_READY, SETTLED]
        async with self.uow_factory() as uow:
            rows = (
                (
                    await uow.session.execute(
                        select(DomainEventOutbox.payload)
                        .where(
                            DomainEventOutbox.event_type.in_(types),
                            DomainEventOutbox.published_at.is_(None),
                            DomainEventOutbox.dead_lettered_at.is_(None),
                        )
                        .limit(_BATCH)
                    )
                )
                .scalars()
                .all()
            )
        for payload in rows:
            if isinstance(payload, dict):
                await self.convert(payload, report)
        return len(rows)

    async def started_at(self) -> datetime:
        """When the drain first ran (recorded on that run)."""
        async with self.uow_factory() as uow:
            state = ReceiverStateRepository(uow.session)
            stored = await state.load(_STARTED)
            if stored:
                return datetime.fromisoformat(stored)
            now = datetime.now(UTC)
            await state.store(_STARTED, now.isoformat())
            return now

    async def outbox_left(self) -> int:
        async with self.uow_factory() as uow:
            return int(
                await uow.session.scalar(
                    select(func.count())
                    .select_from(DomainEventOutbox)
                    .where(
                        DomainEventOutbox.event_type.in_(
                            [WEBHOOK, ONBOARDING_READY, SETTLED]
                        ),
                        DomainEventOutbox.published_at.is_(None),
                        DomainEventOutbox.dead_lettered_at.is_(None),
                    )
                )
                or 0
            )

    async def convert_owed_replies(self, report: DrainReport) -> int:
        from app.modules.agent.contracts import (
            conversation_metadata_records as metadata_records,
        )
        from app.modules.agent.contracts.conversations_for_surfaces import (
            surface_conversation,
        )

        async with self.uow_factory() as uow:
            records = await metadata_records.due_conversation_metadata_records(
                uow,
                key=OWED_REPLY_KEY,
                due_field="next_at",
                due_before=datetime(9999, 1, 1, tzinfo=UTC),
                limit=_BATCH,
            )
            for record in records:
                converted = await _owed_to_delivery(uow, record, surface_conversation)
                report.count("owed_reply" if converted else "owed_reply_skipped")
                await metadata_records.forget_conversation_metadata_record(
                    uow,
                    record.conversation_id,
                    OWED_REPLY_KEY,
                    matching=_identity(record.value),
                )
        return len(records)

    async def arm_open_notifications(self, report: DrainReport) -> None:
        """Once: an expiry item for every notification still open."""
        from app.modules.agent_surfaces.infrastructure.notification_models import (
            NotificationModel,
        )
        from app.modules.agent_surfaces.services.notification_service import (
            _arm_expiry,
        )

        async with self.uow_factory() as uow:
            state = ReceiverStateRepository(uow.session)
            if await state.load(_NOTIFICATIONS_ARMED) == "done":
                return
            await state.store(_NOTIFICATIONS_ARMED, "done")
            rows = (
                (
                    await uow.session.execute(
                        select(NotificationModel).where(
                            NotificationModel.status == "OPEN",
                            NotificationModel.expires_at.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                await _arm_expiry(uow, row.to_entity())
        report.count("notification_expiry", len(rows))


def _field(mapping: Any, name: str) -> Any:
    """A redis-py reply field, whichever of str or bytes its keys came back as."""
    if name in mapping:
        return mapping[name]
    return mapping.get(name.encode())


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return "" if value is None else str(value)


def _identity(value: Any) -> dict[str, str | None]:
    run = value.get("agent_run_id") if isinstance(value, dict) else None
    return {"agent_run_id": str(run) if run else None}


async def _owed_to_delivery(
    uow: SqlAlchemyUnitOfWork, record: Any, surface_conversation: Any
) -> bool:
    value = record.value if isinstance(record.value, dict) else {}
    message = str(value.get("message") or "").strip()
    if not message:
        return False
    conversation = await surface_conversation(uow, record.conversation_id)
    run = value.get("agent_run_id")
    next_at = value.get("next_at")
    due = datetime.fromisoformat(str(next_at)) if next_at else None
    await stage_conversation_message(
        uow,
        idempotency_key=(
            run_reply_idempotency_key(UUID(str(run)))
            if run
            else f"owed:{record.conversation_id}"
        ),
        conversation_id=record.conversation_id,
        pod_id=getattr(conversation, "pod_id", None),
        agent_run_id=UUID(str(run)) if run else None,
        content=DeliveryContent(
            text=message,
            purpose="answer",
            attach_files_of_run=UUID(str(run)) if run else None,
        ),
        not_before=due,
    )
    return True


def make_drain(
    *,
    uow_factory: UnitOfWorkFactory | None = None,
    redis: Any = None,
    min_drain: timedelta = MIN_DRAIN,
):
    async def drain(ctx: JobContext, payload: NoPayload) -> None:
        del payload
        drainer = SurfaceEventDrain(
            uow_factory or SessionUnitOfWorkFactory(async_session_maker), redis
        )
        report = DrainReport()
        left = 0
        try:
            for group in GROUPS:
                ctx.raise_if_stopping()
                left += await drainer.drain_group(group, report)
        except RedisError:
            logger.warning(
                "agent_surfaces.drain.stream_unreadable.degraded", exc_info=True
            )
            left += 1
        left += await drainer.drain_outbox(report)
        left += await drainer.convert_owed_replies(report)
        await drainer.arm_open_notifications(report)
        outbox = await drainer.outbox_left()
        started = await drainer.started_at()
        logger.info(
            "agent_surfaces.drain.progress.observed",
            converted=report.converted,
            entries_read=left,
            outbox_left=outbox,
        )
        if left or outbox or datetime.now(UTC) - started < min_drain:
            raise Defer(_POLL_AGAIN)
        # Caught up for good: the groups are what the cutover check reads, so
        # a drained one goes (its stream stays until the transport is removed).
        for group in GROUPS:
            await destroy_group(drainer._client(), STREAM, group)
        logger.info(
            "agent_surfaces.drain.finished.observed", converted=report.converted
        )

    return drain


def register_drain_work(
    registry: WorkRegistry,
    *,
    uow_factory: UnitOfWorkFactory | None = None,
    redis: Any = None,
    min_drain: timedelta = MIN_DRAIN,
) -> None:
    registry.handler(
        kind=SURFACE_DRAIN_EVENTS,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=NoPayload,
        retry=RetryPolicy(max_attempts=20),
    )(make_drain(uow_factory=uow_factory, redis=redis, min_drain=min_drain))


async def seed_drain(uow: SqlAlchemyUnitOfWork) -> None:
    """Queue the drain (tests; the migration seeds it in SQL)."""
    from app.core.infrastructure.work import SYSTEM_NAMESPACE

    await WorkWriter.of(uow).add(
        SURFACE_DRAIN_EVENTS,
        SYSTEM_NAMESPACE,
        NoPayload(),
        dedup_key="surfaces-drain-events",
    )


register_drain_work(default_registry)
