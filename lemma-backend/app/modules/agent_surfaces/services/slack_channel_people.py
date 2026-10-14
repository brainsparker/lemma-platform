"""Who is in a Slack channel a teammate answers in, as the pod sees them.

Lemma keeps no log of a Slack channel -- Slack has it -- so the group page asks
Slack who is there and says, for each, which of three they are: one of the
pod's people, one of the workspace's (answered as the public is, never a seat),
or from outside the workspace (a guest, or a Slack Connect colleague from
another company). Proof decides "one of the pod's", as everywhere in Slack: a
link made through Lemma's app, or the sender routing has already resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.infrastructure.models import AgentSurfaceExternalUser
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (
    SlackManagerRepository,
)
from app.modules.agent_surfaces.platforms.slack import workspace_calls as slack
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.pod.contracts.members import pod_member_id

#: A channel's first people, read from Slack each time the page asks. Past this
#: the page says how many more there are rather than asking Slack about each.
MAX_CHANNEL_PEOPLE = 50


class ChannelStanding(StrEnum):
    IN_POD = "in"
    WORKSPACE = "workspace"
    OUTSIDE = "outside"


@dataclass(frozen=True)
class ChannelPerson:
    slack_user_id: str
    name: str
    standing: ChannelStanding
    user_id: UUID | None = None


@dataclass(frozen=True)
class ChannelPeople:
    people: list[ChannelPerson]
    #: How many more are in the channel than were looked at.
    more: int = 0


async def channel_people(
    uow: SqlAlchemyUnitOfWork, *, surface: AgentSurfaceEntity, channel_id: str
) -> ChannelPeople:
    credentials = await SurfaceCredentialResolver(uow=uow).for_surface(surface)
    async with connection_released(uow.session):
        ids = await slack.channel_member_ids(
            credentials, channel_id, limit=MAX_CHANNEL_PEOPLE + 1
        )
        looked_at = ids[:MAX_CHANNEL_PEOPLE]
        profiles = {
            user: await slack.user_profile(credentials, user) for user in looked_at
        }
    known = await _known_users(uow, surface.external_workspace_id, looked_at)
    people = []
    for user in looked_at:
        profile = profiles.get(user) or {}
        if profile.get("is_bot") or user == surface.surface_identity_id:
            continue
        user_id = known.get(user)
        in_pod = bool(user_id and await pod_member_id(uow, surface.pod_id, user_id))
        people.append(
            ChannelPerson(
                slack_user_id=user,
                name=_name(profile, user),
                standing=_standing(
                    profile, surface.external_workspace_id, in_pod=in_pod
                ),
                user_id=user_id if in_pod else None,
            )
        )
    order = {
        ChannelStanding.IN_POD: 0,
        ChannelStanding.WORKSPACE: 1,
        ChannelStanding.OUTSIDE: 2,
    }
    people.sort(key=lambda person: (order[person.standing], person.name.lower()))
    return ChannelPeople(people=people, more=max(len(ids) - MAX_CHANNEL_PEOPLE, 0))


async def _known_users(
    uow: SqlAlchemyUnitOfWork, team_id: str | None, slack_user_ids: list[str]
) -> dict[str, UUID]:
    """The Lemma person each Slack user is known to be: proof first, then routing's."""
    if not team_id or not slack_user_ids:
        return {}
    known: dict[str, UUID] = {}
    rows = await uow.session.execute(
        select(
            AgentSurfaceExternalUser.external_user_id,
            AgentSurfaceExternalUser.resolved_user_id,
        ).where(
            AgentSurfaceExternalUser.platform == "SLACK",
            AgentSurfaceExternalUser.tenant_id == team_id,
            AgentSurfaceExternalUser.external_user_id.in_(slack_user_ids),
            AgentSurfaceExternalUser.resolved_user_id.is_not(None),
        )
    )
    for external_user_id, resolved_user_id in rows.all():
        known[str(external_user_id)] = resolved_user_id
    managers = SlackManagerRepository(uow.session)
    for user in slack_user_ids:
        proven = await managers.person(team_id, user)
        if proven is not None:
            known[user] = proven
    return known


def _name(profile: dict[str, object], fallback: str) -> str:
    details = profile.get("profile")
    details = details if isinstance(details, dict) else {}
    for value in (
        details.get("display_name"),
        details.get("real_name"),
        profile.get("name"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _standing(
    profile: dict[str, object], team_id: str | None, *, in_pod: bool
) -> ChannelStanding:
    if in_pod:
        return ChannelStanding.IN_POD
    theirs = str(profile.get("team_id") or "")
    if profile.get("is_restricted") or profile.get("is_ultra_restricted"):
        return ChannelStanding.OUTSIDE
    if theirs and team_id and theirs != team_id:
        return ChannelStanding.OUTSIDE
    return ChannelStanding.WORKSPACE
