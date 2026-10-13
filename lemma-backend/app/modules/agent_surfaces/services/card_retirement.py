"""Taking the buttons off cards a new message cancelled.

A person shown an approval who types something else instead has moved on, and
the turn their message starts denies the pause for them. The card stayed on
screen with live buttons, so a later tap looked like it could still approve --
it was answered "already cancelled", but only after it had been tried. Retiring
the card when the turn starts is what makes the screen match the decision.

After the commit, never inside it: the denial is only real once the turn that
records it has committed, and a platform edit is a network call nobody should
hold a connection across.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.platforms.decision_cards import take_decision_card
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_callback_id,
)

logger = get_logger(__name__)


async def retire_superseded_cards(
    *,
    adapter: SurfacePlatformAdapterPort,
    credentials: dict[str, object],
    conversation_id: UUID,
    tool_call_ids: list[str],
) -> None:
    """Retire the card for each cancelled pause that has one. Never raises."""
    for tool_call_id in tool_call_ids:
        handle = await take_decision_card(
            build_callback_id(conversation_id, tool_call_id)
        )
        if handle is None:
            continue
        try:
            await adapter.retire_decision_card(credentials=credentials, handle=handle)
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
