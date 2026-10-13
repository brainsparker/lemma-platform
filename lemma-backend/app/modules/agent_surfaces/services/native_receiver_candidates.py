"""Which native receivers a deployment should be running, and with what.

One candidate per upstream credential: a Telegram bot to long-poll, a Slack
app to hold a Socket Mode connection for, a Resend key to poll. Split from the
coordinator, which leases and supervises candidates and has no business knowing
how each platform's credentials are spelled. Each platform answers the same two
questions here -- its credentials for a surface with no account of its own, and
the candidate its credentials make -- so adding a platform is one entry in each
table rather than another branch in a chain.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from app.core.config import reveal_secret
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.credentials import (
    CredentialPayload,
    CredentialsLike,
    SlackCredentials,
    SurfaceCredentials,
    TelegramCredentials,
    as_secret,
    credentials_for,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ports import SurfaceAccountPort
from app.modules.agent_surfaces.services.native_receiver_base import (
    NativeReceiverCandidate,
    receiver_key,
)
from app.modules.agent_surfaces.services.resend_polling_receiver import (
    resend_candidate_from_surface,
    resend_receiver_credentials,
)

logger = get_logger(__name__)


def system_telegram_credentials() -> TelegramCredentials | None:
    """The deployment's own bot, when it has one."""
    if not reveal_secret(surface_settings.telegram_bot_token):
        logger.debug(
            "agent_surfaces.event_receiver_service.telegram_system_surface_exists_but.diagnostic"
        )
        return None
    return TelegramCredentials(bot_token=as_secret(surface_settings.telegram_bot_token))


def system_telegram_candidate() -> NativeReceiverCandidate | None:
    """The deployment's shared bot, polled whether or not any surface names it.

    Its key is a fingerprint of its own token like any other, and without a
    stored offset a restart re-reads whatever Telegram still holds.
    """
    token = (reveal_secret(surface_settings.telegram_bot_token) or "").strip()
    if not token:
        return None
    return NativeReceiverCandidate(
        key=receiver_key("telegram", "system", token),
        platform=SurfacePlatform.TELEGRAM,
        surface_ids=(),
        credential_label="system",
        credentials=TelegramCredentials(bot_token=as_secret(token)),
    )


_SYSTEM_CREDENTIALS: dict[SurfacePlatform, Callable[[], SurfaceCredentials | None]] = {
    SurfacePlatform.TELEGRAM: system_telegram_credentials,
    SurfacePlatform.RESEND: resend_receiver_credentials,
}


async def receiver_credentials(
    surface: AgentSurfaceEntity,
    account_port: SurfaceAccountPort,
    account_cache: dict[UUID, CredentialPayload],
) -> SurfaceCredentials | None:
    """The credentials this surface's receiver would run on, or None.

    ``account_cache`` holds each account's stored payload for the scan, so
    surfaces sharing an account read it once.
    """
    if surface.account_id is None:
        system = _SYSTEM_CREDENTIALS.get(surface.surface_type)
        return system() if system is not None else None

    if surface.account_id not in account_cache:
        account = await account_port.get_account(surface.account_id)
        if account is None:
            logger.debug(
                "agent_surfaces.event_receiver_service.native_receiver_skipped_surface_s.diagnostic",
                account_id=surface.account_id,
            )
            return None
        account_cache[surface.account_id] = dict(account.credentials or {})
    credentials = credentials_for(
        surface.surface_type, account_cache[surface.account_id]
    )
    if (
        isinstance(credentials, SlackCredentials)
        and surface.credential_mode is SurfaceCredentialMode.SYSTEM
    ):
        # A workspace on the deployment's own app holds its Socket Mode
        # connection with the deployment's app token, whatever its account says.
        credentials = credentials.model_copy(
            update={"app_token": as_secret(surface_settings.slack_app_token)}
        )
    return credentials


def _telegram_candidate(
    surface: AgentSurfaceEntity, credentials: CredentialsLike
) -> NativeReceiverCandidate | None:
    telegram = TelegramCredentials.of(credentials)
    bot_token = (reveal_secret(telegram.bot_token) or "").strip()
    if not bot_token:
        logger.debug(
            "agent_surfaces.event_receiver_service.telegram_native_receiver_skipped_surface.diagnostic"
        )
        return None
    credential_label = str(surface.account_id) if surface.account_id else "system"
    return NativeReceiverCandidate(
        key=receiver_key("telegram", credential_label, bot_token),
        platform=SurfacePlatform.TELEGRAM,
        surface_ids=(surface.id,),
        credential_label=credential_label,
        credentials=telegram,
    )


def _slack_candidate(
    surface: AgentSurfaceEntity, credentials: CredentialsLike
) -> NativeReceiverCandidate | None:
    slack = SlackCredentials.of(credentials)
    app_token = (reveal_secret(slack.app_token) or "").strip()
    if not app_token:
        logger.debug(
            "agent_surfaces.event_receiver_service.slack_native_receiver_skipped_surface.diagnostic"
        )
        return None
    credential_label = (
        "system"
        if surface.credential_mode is SurfaceCredentialMode.SYSTEM
        else str(surface.account_id)
    )
    return NativeReceiverCandidate(
        key=receiver_key("slack", credential_label, app_token),
        platform=SurfacePlatform.SLACK,
        surface_ids=(surface.id,),
        credential_label=credential_label,
        credentials=slack,
    )


_CANDIDATES: dict[
    SurfacePlatform,
    Callable[[AgentSurfaceEntity, CredentialsLike], NativeReceiverCandidate | None],
] = {
    SurfacePlatform.TELEGRAM: _telegram_candidate,
    SurfacePlatform.SLACK: _slack_candidate,
    SurfacePlatform.RESEND: resend_candidate_from_surface,
}


def candidate_from_surface(
    surface: AgentSurfaceEntity, credentials: CredentialsLike
) -> NativeReceiverCandidate | None:
    """The receiver these credentials make for this surface, if its platform
    has native receivers at all."""
    build = _CANDIDATES.get(surface.surface_type)
    return build(surface, credentials) if build is not None else None


__all__ = [
    "candidate_from_surface",
    "receiver_credentials",
    "system_telegram_candidate",
]
