"""What a run is shown of the conversation around it, and who else reads it.

A group run is given the recent lines of its group as background -- the pod's
own log where it keeps one, otherwise the platform's history read live -- and
told who outside the pod reads the chat. An email run is told who a reply-all
reaches. Free functions over a unit-of-work factory, each opening its own short
scope, because `SurfaceTurnStarter` gathers this between platform calls and
holds no connection across them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.adapters.user_directory_adapter import (
    IdentityUserDirectoryAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.platforms.resend.email_recipients import (
    reply_all_recipients,
)
from app.modules.agent_surfaces.services.group_audience import (
    Audience,
    chat_audience,
    email_audience,
    outside_names,
)
from app.modules.agent_surfaces.services.group_log import (
    GroupBackground,
    for_member_run,
    group_background,
    keeps_group_log,
)
from app.modules.agent_surfaces.services.surface_inbound_message import (
    fetch_channel_context,
)


@dataclass(frozen=True, slots=True)
class RunBackground:
    """The group as one run is shown it."""

    lines: list[dict[str, object]]
    withheld: int = 0
    audience: Audience | None = None


async def run_background(
    uow_factory: UnitOfWorkFactory,
    context: SurfaceChatContext,
    *,
    adapter: SurfacePlatformAdapterPort,
    credentials: dict[str, object],
) -> RunBackground:
    """What this run is shown of the group, and who outside the pod reads it.

    The pod's own log where it keeps one (Telegram, WhatsApp); otherwise the
    platform's history, read live. Either way a member's run is not shown
    what strangers wrote (``group_log``), and is told that they are there.
    """
    group, logged = await _group_background(uow_factory, context)
    if logged is not None:
        lines = [line.model_dump(mode="json") for line in logged.lines]
        withheld, outside = logged.withheld, list(logged.outside_authors)
    else:
        lines = await fetch_channel_context(
            adapter=adapter, context=context, credentials=credentials
        )
        outside = outside_names(lines)
        withheld = 0
        if not context.answers_outsider:
            lines, withheld = for_member_run(lines)
    # The message being answered was logged on its way in and is already
    # the prompt; repeating it as background shows the agent the question
    # twice. It is the latest line in all but a race, so only that one goes.
    if lines and lines[-1].get("text") == context.event.message_text.strip():
        lines.pop()
    audience = (
        None
        if context.answers_outsider
        else chat_audience(
            platform=context.platform,
            group=group,
            parsed=context.event,
            outside_authors=outside,
        )
    )
    return RunBackground(lines=lines, withheld=withheld, audience=audience)


async def _group_background(
    uow_factory: UnitOfWorkFactory, context: SurfaceChatContext
) -> tuple[SurfaceGroup | None, GroupBackground | None]:
    """The group the pod keeps a log for, and that log; else nothing.

    Nothing where the platform can read its history (Slack, Teams) or the
    group is not one the pod keeps a log for -- the caller then asks the
    platform, as it always has, and reads a shared Slack channel off the
    event itself.
    """
    channel_id = context.event.external_channel_id
    if (
        context.surface_id is None
        or not channel_id
        or not keeps_group_log(context.platform.value)
    ):
        return None, None
    async with uow_factory() as uow:
        group = await SurfaceGroupRepository(uow.session).get(
            surface_id=context.surface_id, external_channel_id=channel_id
        )
        if group is None:
            return None, None
        logged = await group_background(
            uow,
            group=group,
            membership=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
            agent_display_name=context.agent_display_name,
            for_stranger=context.answers_outsider,
        )
    return group, logged


async def reply_all_audience(
    uow_factory: UnitOfWorkFactory, context: SurfaceChatContext
) -> Audience | None:
    """Who outside the pod a reply on this email thread also reaches."""
    if context.platform is not SurfacePlatform.RESEND or context.pod_id is None:
        return None
    recipients = reply_all_recipients(context.event)
    if not recipients:
        return None
    pod_id = context.pod_id
    async with uow_factory() as uow:
        people = IdentityUserDirectoryAdapter(uow)
        membership = SqlAlchemySurfaceRoutingResolutionAdapter(uow)

        async def is_member(address: str) -> bool:
            user_id = await people.user_id_by_email(address)
            return user_id is not None and pod_id in set(
                await membership.get_user_pod_ids(user_id)
            )

        return await email_audience(recipients=recipients, is_member=is_member)
