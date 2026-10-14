"""Putting one ``surface_deliveries`` row on its platform: the network half.

The pump (``events/reply_work``) claims a row, calls :func:`send_delivery`
with no session held, and records what it learned. ``send_delivery`` never
raises for a platform failure: it says whether the message arrived and, when
not, whether the platform called the refusal transient and how long it asked
to be left alone.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.run_replies import run_reply_material
from app.modules.agent_surfaces.domain.deliveries import (
    ConversationTarget,
    DeliveryContent,
    InboundSenderTarget,
    ReplyContextTarget,
    parse_target,
)
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.infrastructure.inbound_delivery_models import (
    SurfaceDeliveryModel,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.inbound_message_repository import (  # noqa: E501
    InboundMessageRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.platforms.delivery import track_sends
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress
from app.modules.agent_surfaces.services.pending_envelope import (
    RunFiles,
    discard_display_paths,
)
from app.modules.agent_surfaces.services.progress_events import failure_notice
from app.modules.agent_surfaces.services.surface_deliveries import SendResult

logger = get_logger(__name__)

#: What one send can fail with and still be an outcome rather than a bug.
_SEND_ERRORS: tuple[type[BaseException], ...] = (
    AgentSurfaceError,
    SQLAlchemyError,
    *PLATFORM_TRANSPORT_ERRORS,
)

#: Sends one claimed row; the seam a test replaces with a fake platform.
type DeliverySender = Callable[[SurfaceDeliveryModel], Awaitable[SendResult]]

ALL_REASONING_REPLY = (
    "I thought that through but never wrote the answer. "
    "Ask me again and I'll give it another go."
)


class PlatformDeliverySender:
    """The real sender: egress for a thread, the fallback path for a sender."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        egress_factory: Callable[[SqlAlchemyUnitOfWork], SurfaceEgress],
    ) -> None:
        self.uow_factory = uow_factory
        self.egress_factory = egress_factory

    async def __call__(self, row: SurfaceDeliveryModel) -> SendResult:
        target = parse_target(row.target)
        content = DeliveryContent.model_validate(row.content)
        if isinstance(target, ConversationTarget):
            return await self._to_conversation(row, target, content)
        if isinstance(target, ReplyContextTarget):
            return await self._reply(SurfaceReplyContext.model_validate(target.context))
        return await self._to_inbound_sender(target, content)

    async def _reply(self, context: SurfaceReplyContext) -> SendResult:
        """A prepared reply outside any run, through the fallback path."""
        from app.modules.agent_surfaces.services.turn_starter import (
            SurfaceTurnStarter,
        )

        try:
            with track_sends() as outcome:
                await SurfaceTurnStarter(uow_factory=self.uow_factory).execute_chat(
                    context
                )
        except _SEND_ERRORS as exc:
            return SendResult(delivered=False, error=f"{type(exc).__name__}: {exc}")
        return SendResult(
            delivered=True,
            transient=outcome.gave_up_transient,
            retry_after=outcome.retry_after,
        )

    async def _rendered(
        self, row: SurfaceDeliveryModel, content: DeliveryContent
    ) -> str | None:
        """The text to send: as written, or -- for a run's backstop -- from the run."""
        if content.text.strip():
            return content.text
        if row.agent_run_id is None:
            return None
        async with self.uow_factory() as uow:
            material = await run_reply_material(uow, row.agent_run_id)
            platform = None
            if row.surface_id is not None:
                surface = await SurfaceRepository(uow).get(row.surface_id)
                platform = surface.surface_type.value if surface else None
        if material is None or material.private:
            return None
        if content.purpose == "failure":
            return failure_notice(material.error, platform)
        return material.answer

    async def _to_conversation(
        self,
        row: SurfaceDeliveryModel,
        target: ConversationTarget,
        content: DeliveryContent,
    ) -> SendResult:
        text = await self._rendered(row, content)
        if not text:
            return SendResult(delivered=False, error="nothing to send")
        run = (
            RunFiles(content.attach_files_of_run)
            if content.attach_files_of_run is not None
            else None
        )
        with track_sends() as outcome:
            try:
                async with self.uow_factory() as uow:
                    egress = self.egress_factory(uow)
                    if content.plain:
                        delivered = await _send_plain(
                            egress, target.conversation_id, text
                        )
                    else:
                        delivered = await egress.send_agent_message_for_conversation(
                            conversation_id=target.conversation_id,
                            message=text,
                            metadata=dict(content.metadata) or None,
                            attach_files_of=run,
                        )
                error = None if delivered else "no surface thread to send to"
            except _SEND_ERRORS as exc:
                logger.warning(
                    "agent_surfaces.delivery_sender.send_failed.degraded",
                    delivery_id=str(row.id),
                    conversation_id=str(target.conversation_id),
                    exc_info=True,
                )
                delivered = False
                error = f"{type(exc).__name__}: {exc}"
        if run is not None and (delivered or not outcome.gave_up_transient):
            # Released with the reply that carried them, or with a reply that
            # will not be tried again; a retry still needs them.
            await discard_display_paths(target.conversation_id, run)
        return SendResult(
            delivered=delivered,
            transient=outcome.gave_up_transient,
            partial=outcome.partial,
            retry_after=outcome.retry_after,
            error=error,
        )

    async def _to_inbound_sender(
        self, target: InboundSenderTarget, content: DeliveryContent
    ) -> SendResult:
        """Tell the sender of a stored message that it was not processed."""
        from app.modules.agent_surfaces.services.turn_starter import (
            SurfaceTurnStarter,
        )

        async with self.uow_factory() as uow:
            row = await InboundMessageRepository(uow.session).get(
                target.inbound_message_id
            )
            if row is None:
                return SendResult(delivered=False, error="inbound message is gone")
            source, payload, headers = row.source, dict(row.payload), dict(row.headers)
            surface_id = row.surface_id
            account_id = None
            if surface_id is not None:
                surface = await SurfaceRepository(uow).get(surface_id)
                account_id = surface.account_id if surface is not None else None
        platform = SurfacePlatform.from_source(source)
        adapters = SurfacePlatformAdapterRegistry()
        adapter = adapters.get(platform) if platform else None
        if platform is None or adapter is None:
            return SendResult(delivered=False, error="no adapter")
        try:
            parsed = await adapter.parse_inbound_event(payload, headers)
            if parsed is None or not parsed.is_dm or not parsed.sender_external_user_id:
                # A group is left alone: a reply there is read by everyone in
                # it, about one person's message.
                return SendResult(delivered=True)
            logger.warning(
                "agent_surfaces.lost_delivery.sender_told.degraded",
                source=source,
                surface_id=str(surface_id) if surface_id else None,
            )
            with track_sends() as outcome:
                await SurfaceTurnStarter(
                    uow_factory=self.uow_factory, adapter_registry=adapters
                ).execute_chat(
                    SurfaceReplyContext(
                        platform=platform,
                        surface_id=surface_id,
                        surface_account_id=account_id,
                        event=parsed,
                        reply_kind="processing_failed",
                        reply_message=content.text,
                    )
                )
        except _SEND_ERRORS as exc:
            return SendResult(delivered=False, error=f"{type(exc).__name__}: {exc}")
        return SendResult(
            delivered=True,
            transient=outcome.gave_up_transient,
            retry_after=outcome.retry_after,
        )


async def _send_plain(egress: SurfaceEgress, conversation_id: UUID, text: str) -> bool:
    """A message as written, into the thread -- no agent formatting, no files."""
    target = await egress.delivery.resolve_egress_target(conversation_id)
    if target is None:
        return False
    # No connection held for the platform call; see `connection_released`.
    async with connection_released(egress.delivery.uow.session):
        await target.adapter.send_message(
            credentials=target.credentials, event=target.event, message=text
        )
    return True


__all__ = ["ALL_REASONING_REPLY", "DeliverySender", "PlatformDeliverySender"]
