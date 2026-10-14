"""The cutover's converters for what the old transport owed the surfaces.

* ``agent-surfaces.webhook`` -- a webhook already answered 200 to its
  platform, still owed in the outbox: accepted as the request path accepts one
  today (``InboundAcceptor``), whose ``(receiver_scope, source_event_id)``
  unique row makes a second delivery of it a no-op.
* ``agent-surfaces.onboarding`` -- a pending onboarding that became ready:
  handed off (``hand_off_when_ready``, which acts only while ``handed_off_at``
  is unset).
* ``streaq:process_surface_message`` -- a parsed message whose job only Redis
  held: answered through the turn starter that ``surface.ingest`` uses.
* ``state:surfaces.owed_replies`` -- once, at the end: every reply owed in
  conversation metadata (``surface_owed_reply``, the old owed-reply job's
  record) becomes a PENDING ``surface_deliveries`` row due when the record
  was, keyed like the run's own reply, and the record is cleared.
* ``state:surfaces.notification_expiry`` -- once, at the end: an expiry item
  for every open notification (the old cron's job; new ones get theirs with
  the row).

Worker only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select

from app.core.infrastructure.cutover.routes import LegacyPayload, legacy_converter
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import JobContext
from app.core.log.log import get_logger
from app.modules.agent.contracts.conversation_metadata_records import MetadataRecord
from app.modules.agent_surfaces.domain.deliveries import DeliveryContent
from app.modules.agent_surfaces.domain.inbound_messages import SHARED_RECEIVER_SCOPE
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)

logger = get_logger(__name__)

OWED_REPLY_KEY = "surface_owed_reply"
_BATCH = 200


def _uow_factory() -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@legacy_converter("agent-surfaces.webhook")
async def webhook(ctx: JobContext, event: LegacyPayload, route: str) -> None:
    del ctx, route
    from app.modules.agent_surfaces.services.inbound_acceptor import (
        InboundAcceptor,
        InboundDelivery,
    )

    source = str(event.get("source") or "")
    payload = event.get("payload") or {}
    headers = dict(event.get("headers") or {})
    surface_id = event.get("surface_id")
    receivers = event.get("receiver_surface_ids")
    source_event_id = str(event.get("source_event_id") or event.get("event_id") or "")
    if not source or not isinstance(payload, dict) or not source_event_id:
        logger.warning(
            "agent_surfaces.cutover.webhook_unreadable",
            event_id=str(event.get("event_id")),
        )
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
    await InboundAcceptor(_uow_factory()).accept(
        InboundDelivery(
            request=request, receiver_scope=scope, source_event_id=source_event_id
        )
    )


@legacy_converter("agent-surfaces.onboarding")
async def onboarding_ready(ctx: JobContext, event: LegacyPayload, route: str) -> None:
    del route
    from app.modules.agent_surfaces.services.onboarding_handoff import (
        hand_off_when_ready,
    )

    if not event.get("pending_id"):
        return
    async with ctx.complete_work() as uow:
        await hand_off_when_ready(uow, UUID(str(event["pending_id"])))


@legacy_converter("streaq:process_surface_message")
async def surface_message(ctx: JobContext, event: LegacyPayload, route: str) -> None:
    del ctx, route
    from app.modules.agent_surfaces.composition import build_surface_turn_starter
    from app.modules.agent_surfaces.domain.ingress_context import (
        AgentSurfaceContext,
    )

    args = list(event.get("args") or [])
    payload = dict(event.get("kwargs") or {}).get("payload") or (
        args[0] if args else None
    )
    if not isinstance(payload, dict) or not payload.get("context"):
        return
    context = TypeAdapter(AgentSurfaceContext).validate_python(payload["context"])
    await build_surface_turn_starter(_uow_factory()).execute_chat(context)


@legacy_converter("state:surfaces.owed_replies")
async def owed_replies(ctx: JobContext, event: LegacyPayload, route: str) -> None:
    del event, route
    from app.modules.agent.contracts import (
        conversation_metadata_records as metadata_records,
    )

    while True:
        ctx.raise_if_stopping()
        async with ctx.checkpoint() as uow:
            records = await metadata_records.due_conversation_metadata_records(
                uow,
                key=OWED_REPLY_KEY,
                due_field="next_at",
                due_before=datetime(9999, 1, 1, tzinfo=UTC),
                limit=_BATCH,
            )
            for record in records:
                await _owed_to_delivery(uow, record)
                await metadata_records.forget_conversation_metadata_record(
                    uow,
                    record.conversation_id,
                    OWED_REPLY_KEY,
                    matching=_identity(record.value),
                )
        if len(records) < _BATCH:
            return


@legacy_converter("state:surfaces.notification_expiry")
async def notification_expiry(
    ctx: JobContext, event: LegacyPayload, route: str
) -> None:
    del event, route
    from app.modules.agent_surfaces.infrastructure.notification_models import (
        NotificationModel,
    )
    from app.modules.agent_surfaces.services.notification_service import _arm_expiry

    after = UUID(int=0)
    while True:
        async with ctx.checkpoint() as uow:
            rows = (
                (
                    await uow.session.execute(
                        select(NotificationModel)
                        .where(
                            NotificationModel.status == "OPEN",
                            NotificationModel.expires_at.is_not(None),
                            NotificationModel.id > after,
                        )
                        .order_by(NotificationModel.id)
                        .limit(_BATCH)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                await _arm_expiry(uow, row.to_entity())
        if len(rows) < _BATCH:
            return
        after = rows[-1].id


def _identity(value: object) -> dict[str, str | None]:
    run = value.get("agent_run_id") if isinstance(value, dict) else None
    return {"agent_run_id": str(run) if run else None}


async def _owed_to_delivery(uow: SqlAlchemyUnitOfWork, record: MetadataRecord) -> bool:
    from app.modules.agent.contracts.conversations_for_surfaces import (
        surface_conversation,
    )
    from app.modules.agent_surfaces.services.surface_deliveries import (
        run_reply_idempotency_key,
        stage_conversation_message,
    )

    value = record.value if isinstance(record.value, dict) else {}
    message = str(value.get("message") or "").strip()
    if not message:
        return False
    conversation = await surface_conversation(uow, record.conversation_id)
    run = value.get("agent_run_id")
    next_at = value.get("next_at")
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
        not_before=datetime.fromisoformat(str(next_at)) if next_at else None,
    )
    return True
