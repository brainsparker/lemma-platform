"""The account a surface speaks through, as channels key its places.

A Slack app in a workspace, a Teams bot in a tenant, a Telegram bot, an
inbox: the thing on the platform that outlives one installation of the bot.
A place heard by a bot that was removed and installed again is still in the
same account, so its channel keeps its history.

Slack and Teams are keyed by the workspace (tenant) *and* the bot: two agents'
apps installed in one workspace are two accounts, each with its own places,
token and history -- one row between them had the last app to speak take the
other's places over. A reinstall of the same app keeps its bot id, so it keeps
its account. A surface that has not recorded its bot yet is keyed by the
workspace alone, as before.

A surface whose platform names no such account (a WhatsApp number, a web
widget) is its own.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)


def account_key_for(surface: AgentSurfaceEntity) -> str:
    """The account a surface speaks through, as its platform names it."""
    platform = surface.surface_type
    bot = (surface.surface_identity_id or "").strip()
    if platform is SurfacePlatform.SLACK and surface.external_workspace_id:
        team = f"team:{surface.external_workspace_id}"
        return f"{team}:bot:{bot}" if bot else team
    if platform is SurfacePlatform.TEAMS and surface.external_tenant_id:
        tenant = f"tenant:{surface.external_tenant_id}"
        return f"{tenant}:bot:{bot}" if bot else tenant
    if platform is SurfacePlatform.TELEGRAM and bot:
        return f"bot:{bot}"
    if platform is SurfacePlatform.RESEND and surface.surface_identity_email:
        return f"inbox:{surface.surface_identity_email.strip().lower()}"
    return f"surface:{surface.id}"
