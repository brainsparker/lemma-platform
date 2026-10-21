"""What Lemma's own Slack app says that must not be lost, staged as work.

The manager belongs to an organization, not a pod, and has no channel of its
own to deliver through, so its messages stay Slack-native (a DM from its bot)
rather than channel lines. A message that is owed -- "X is in Slack now", a
teammate's first word in the channel it was handed -- is a work item written
in the transaction that makes it owed and keyed by its cause, so it exists
exactly when that change commits and is sent once. A retry of the change
finds the key queued and adds nothing.

Interactive answers (views, ephemerals, a replaced button message) are not
staged: they answer the person who just acted, and a retried event answers
again at most.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from pydantic import JsonValue

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter, org_namespace
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.work_payloads import (
    SlackManagerMessagePayload,
    SlackTeammateHelloPayload,
)
from app.modules.agent_surfaces.events.work_kinds import (
    SURFACE_SLACK_MANAGER_MESSAGE,
    SURFACE_SLACK_TEAMMATE_HELLO,
    slack_manager_message_key,
    slack_teammate_hello_key,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.slack import workspace_calls as slack
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.group_hello import say_hello_in_group
from app.modules.agent_surfaces.services.slack_manager_context import load_context

logger = get_logger(__name__)


class SlackSendDeferred(Exception):
    """Slack said "not now" (a rate limit, an outage): the item tries again."""


async def stage_manager_message(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_id: UUID,
    team_id: str,
    slack_user_id: str | None,
    text: str,
    cause: str,
    blocks: Sequence[Mapping[str, JsonValue]] | None = None,
    refresh_home: bool = False,
) -> None:
    """Owe this person a DM from the manager, in ``uow``'s transaction.

    ``cause`` names why, once: ``welcome:<install id>``, ``allowed:<account id>``.
    """
    if not slack_user_id:
        return
    await WorkWriter.of(uow).add(
        SURFACE_SLACK_MANAGER_MESSAGE,
        org_namespace(organization_id),
        SlackManagerMessagePayload(
            team_id=team_id,
            slack_user_id=slack_user_id,
            text=text,
            blocks=[dict(block) for block in blocks] if blocks else None,
            refresh_home=refresh_home,
        ),
        dedup_key=slack_manager_message_key(cause),
    )


async def send_manager_message(
    uow: SqlAlchemyUnitOfWork, payload: SlackManagerMessagePayload
) -> None:
    """Send one owed DM. Lemma removed from the workspace since: nothing to send."""
    from app.modules.agent_surfaces.services.slack_manager import refresh_home

    ctx = await load_context(
        uow, team_id=payload.team_id, slack_user_id=payload.slack_user_id
    )
    if ctx is None:
        return
    async with connection_released(uow.session):
        channel, error = await slack.open_dm(ctx.credentials, payload.slack_user_id)
        if channel:
            error = await slack.post_message(
                ctx.credentials,
                channel=channel,
                text=payload.text,
                blocks=payload.blocks,
            )
    if error in slack.TRANSIENT_ERRORS:
        raise SlackSendDeferred(error)
    if payload.refresh_home:
        # The DM is out, and only a deferred send retries: a failed refresh
        # retrying the item would send it again.
        await refresh_home(uow, ctx)


async def stage_teammate_hello(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_id: UUID,
    surface_id: UUID,
    channel_id: str,
) -> None:
    """Owe the channel the teammate's hello, in ``uow``'s transaction."""
    await WorkWriter.of(uow).add(
        SURFACE_SLACK_TEAMMATE_HELLO,
        org_namespace(organization_id),
        SlackTeammateHelloPayload(surface_id=surface_id, channel_id=channel_id),
        dedup_key=slack_teammate_hello_key(surface_id, channel_id),
    )


async def say_teammate_hello(
    uow: SqlAlchemyUnitOfWork, payload: SlackTeammateHelloPayload
) -> None:
    """The teammate's own bot says main's group hello: who it is, that people
    outside the pod get Public answers, and that the pod keeps what is said."""
    surface = await SurfaceRepository(uow).get(payload.surface_id)
    if surface is None:
        return
    adapter = SurfacePlatformAdapterRegistry().get(surface.surface_type)
    if adapter is None:
        return
    credentials = await SurfaceCredentialResolver(uow=uow).for_surface(surface)
    await say_hello_in_group(
        uow,
        surface=surface,
        adapter=adapter,
        credentials=credentials,
        channel_id=payload.channel_id,
    )
