from __future__ import annotations

from uuid import UUID

from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.identity.contracts.surfaces import live_user_telegram_username

logger = get_logger(__name__)


async def creator_is_confirmed(
    *, uow, user_id: UUID, telegram_username: str | None
) -> bool:
    """Has the Lemma user who started setup named this Telegram account?

    The setup link is the only thing tying a Telegram account to the Lemma
    user, and a link can leak -- a screenshot, a shared screen. Whoever opens
    it first would be bound to that user for good, and their messages would
    run with that user's authority. So the binding needs the user's own word
    for which account is theirs, and the one they can give without another
    round trip is the Telegram handle on their profile: a stranger who opens
    the link cannot write it there. Telegram vouches for the handle the
    opener writes with, since the manager webhook is verified.
    """
    opened_by = str(telegram_username or "").strip().lstrip("@").lower()
    declared = await live_user_telegram_username(uow, user_id)
    return bool(opened_by) and opened_by == str(declared or "").lstrip("@")


async def link_managed_bot_creator(
    *,
    uow,
    telegram_user_id: int | None,
    telegram_username: str | None,
    telegram_display_name: str | None,
    setup_id: str,
    user_id: UUID,
) -> bool:
    """Bind the account that created the bot to its Lemma user, if confirmed.

    Returns whether the binding was written. Unconfirmed, the bot is still
    connected; its creator is recognised the way any other sender is.
    """
    if telegram_user_id is None:
        raise DomainError(
            "Telegram setup is missing its creator identity",
            code="TELEGRAM_CREATOR_IDENTITY_MISSING",
        )
    external_users = ExternalSurfaceUserRepository(uow)
    existing = await external_users.get_by_identity(
        platform=SurfacePlatform.TELEGRAM.value,
        tenant_id=None,
        external_user_id=str(telegram_user_id),
    )
    if (
        existing is not None
        and existing.resolved_user_id is not None
        and existing.resolved_user_id != user_id
    ):
        raise DomainError(
            "This Telegram account is already linked to another Lemma user",
            code="TELEGRAM_IDENTITY_ALREADY_LINKED",
            status_code=409,
        )
    if not await creator_is_confirmed(
        uow=uow, user_id=user_id, telegram_username=telegram_username
    ):
        logger.info(
            "agent_surfaces.managed_bot.creator_binding_unconfirmed",
            setup_id=setup_id,
        )
        return False
    await external_users.upsert(
        platform=SurfacePlatform.TELEGRAM.value,
        tenant_id=None,
        external_user_id=str(telegram_user_id),
        email=None,
        phone=None,
        display_name=telegram_display_name,
        raw_profile={
            "sender_username": telegram_username,
            "managed_bot_setup_id": setup_id,
        },
        resolved_user_id=user_id,
    )
    return True
