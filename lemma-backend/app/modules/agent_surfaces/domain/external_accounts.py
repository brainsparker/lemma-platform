"""The account a surface speaks through, as channels key its places.

A Slack workspace, a Teams tenant, a Telegram bot, an inbox: the thing on the
platform that outlives one installation of the bot. A place heard by a bot
that was removed and installed again is still in the same account, so its
channel keeps its history. A surface whose platform names no such account (a
WhatsApp number, a web widget) is its own.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)


def account_key_for(surface: AgentSurfaceEntity) -> str:
    """The account a surface speaks through, as its platform names it."""
    platform = surface.surface_type
    if platform is SurfacePlatform.SLACK and surface.external_workspace_id:
        return f"team:{surface.external_workspace_id}"
    if platform is SurfacePlatform.TEAMS and surface.external_tenant_id:
        return f"tenant:{surface.external_tenant_id}"
    if platform is SurfacePlatform.TELEGRAM and surface.surface_identity_id:
        return f"bot:{surface.surface_identity_id}"
    if platform is SurfacePlatform.RESEND and surface.surface_identity_email:
        return f"inbox:{surface.surface_identity_email.strip().lower()}"
    return f"surface:{surface.id}"
