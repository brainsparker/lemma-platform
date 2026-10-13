"""Taking the buttons off cards whose pause is over.

A person shown an approval who types something else instead has moved on, and
the turn their message starts denies the pause for them. The card stayed on
screen with live buttons, so a later tap looked like it could still approve --
it was answered "already cancelled", but only after it had been tried. Retiring
the card when the turn starts is what makes the screen match the decision.

The same for a pause answered by a typed reply: the answer is in, and the
card's buttons would only offer to answer it again.

After the commit, never inside it: the denial is only real once the turn that
records it has committed, and a platform edit is a network call nobody should
hold a connection across.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials
from app.modules.agent_surfaces.domain.decision_card import (
    CardRetirement,
    DecisionCardHandle,
)
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.platforms.decision_cards import take_decision_cards
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_callback_id,
)

logger = get_logger(__name__)


async def retire_superseded_cards(
    *,
    adapter: SurfacePlatformAdapterPort,
    credentials: SurfaceCredentials,
    conversation_id: UUID,
    tool_call_ids: list[str],
    reason: CardRetirement = CardRetirement.SUPERSEDED,
) -> None:
    """Retire every card of each pause that has any. Never raises."""
    for tool_call_id in tool_call_ids:
        for handle in await take_decision_cards(
            build_callback_id(conversation_id, tool_call_id)
        ):
            await _retire(adapter, credentials, conversation_id, handle, reason)


async def _retire(
    adapter: SurfacePlatformAdapterPort,
    credentials: SurfaceCredentials,
    conversation_id: UUID,
    handle: DecisionCardHandle,
    reason: CardRetirement,
) -> None:
    try:
        await adapter.retire_decision_card(
            credentials=credentials, handle=handle, reason=reason
        )
    except PLATFORM_TRANSPORT_ERRORS:
        # The card keeps its buttons, and a tap on it is still answered
        # "already cancelled" -- worse than retiring it, not wrong.
        logger.warning(
            "agent_surfaces.card_retirement.card_not_retired.degraded",
            conversation_id=str(conversation_id),
            platform=handle.platform,
            exc_info=True,
        )


__all__ = ["retire_superseded_cards"]
