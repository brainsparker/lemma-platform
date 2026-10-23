"""Who a required per-member connector is still waiting on, and asking them.

Design §3.7: an editor sees "5/7 connected" and who is missing, and can ask
them to connect. Both answers are about other people, so both keep to what an
editor may know of them: who they are and whether they have connected --
never which account, and never anything about it.

**Who counts.** Only members who may use the connector: a POD_VIEWER, or
anyone a RESTRICTED connector was not granted to, is not "missing" an account
for something they could not run. Each member's own authority answers that,
through the same authorizer a call of theirs would meet.

**Asking.** One line in each member's own channel with the pod -- the path the
agents' ``channel_post`` takes: the pod's agent speaking for the editor who
asked. Not a new notification system: channels already decides how a member
hears of a line in their channel. A member is asked about one connector at most
once a day, whoever asks, which is the ledger's job (Redis, so every replica
sees the same day).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    AskMembersResponse,
    MemberAwaited,
    MemberConnectStanding,
    PodConnectorCoverage,
    PodConnectorResponse,
)
from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.domain.pod_connector import (
    PodConnectorEntity,
    PodConnectorMode,
    PodConnectorStatus,
)
from app.modules.connectors.domain.pod_connector_errors import (
    PodConnectorNotFoundError,
    PodConnectorValidationError,
)
from app.modules.connectors.infrastructure.repositories.member_standing_queries import (
    member_account_statuses,
)
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodConnectorRepository,
)
from app.modules.connectors.services.pod_connector_resolution import (
    acting_person,
    connect_url,
)
from app.modules.pod.contracts.people import MAX_POD_PEOPLE, PodPerson


class PodPeople(Protocol):
    async def __call__(
        self, uow: SqlAlchemyUnitOfWork, pod_id: UUID, *, limit: int
    ) -> list[PodPerson]: ...


class MemberContext(Protocol):
    """A member's own authority in the pod, as a request of theirs would hold it."""

    async def __call__(
        self, uow: SqlAlchemyUnitOfWork, *, user_id: UUID, pod_id: UUID
    ) -> Context: ...


class AskLedger(Protocol):
    """Once a day per connector per member. ``claim`` is True the first time."""

    async def claim(self, *, pod_connector_id: UUID, user_id: UUID) -> bool | None: ...


class TellMember(Protocol):
    """One line in a member's own channel with the pod, said for ``asked_by``."""

    async def __call__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        pod_id: UUID,
        user_id: UUID,
        asked_by: UUID,
        text: str,
        key: str,
    ) -> None: ...


#: The catalog title of an installation's connector -- "Gmail" -- if known.
ConnectorTitle = Callable[[UUID], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class _Count:
    coverage: PodConnectorCoverage
    #: Everyone counted, so asking can skip the lookup.
    waiting_on: list[MemberAwaited]


class PodConnectorCoverageService:
    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        pod_connectors: PodConnectorRepository,
        people: PodPeople,
        member_context: MemberContext,
        ledger: AskLedger,
        tell: TellMember,
        connector_title: ConnectorTitle,
    ):
        self.uow = uow
        self.pod_connectors = pod_connectors
        self._people = people
        self._member_context = member_context
        self._ledger = ledger
        self._tell = tell
        self._connector_title = connector_title
        #: Each member's authority, built once per request however many
        #: connectors are counted.
        self._contexts: dict[UUID, Context] = {}

    # -- coverage -------------------------------------------------------------

    async def with_coverage(
        self, ctx: Context, pod_id: UUID, items: list[PodConnectorResponse]
    ) -> list[PodConnectorResponse]:
        """``items``, each required per-member one with its coverage -- when
        the caller may manage the pod's connectors. Unchanged otherwise."""
        counted = [item for item in items if _counted(item)]
        if not counted or not await ctx.can(
            Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id)
        ):
            return items
        people = await self._people(self.uow, pod_id, limit=MAX_POD_PEOPLE + 1)
        coverage = {
            item.id: (await self._count(pod_id, item, people)).coverage
            for item in counted
        }
        return [
            item.model_copy(update={"member_coverage": coverage[item.id]})
            if item.id in coverage
            else item
            for item in items
        ]

    async def _count(
        self,
        pod_id: UUID,
        connector: PodConnectorResponse | PodConnectorEntity,
        people: list[PodPerson],
    ) -> _Count:
        truncated = len(people) > MAX_POD_PEOPLE
        people = people[:MAX_POD_PEOPLE]
        ref = ResourceRef.connector(pod_id, connector.id)
        eligible = [
            person
            for person in people
            if await (await self._context(person.user_id, pod_id)).can(
                Permissions.CONNECTOR_USE, ref
            )
        ]
        statuses = await member_account_statuses(
            self.uow.session,
            pod_id=pod_id,
            auth_config_id=connector.auth_config_id,
            user_ids=[person.user_id for person in eligible],
        )
        waiting_on = [
            MemberAwaited(
                user_id=person.user_id,
                pod_member_id=person.pod_member_id,
                name=person.name,
                email=person.email,
                standing=standing,
            )
            for person in eligible
            if (standing := _standing(statuses.get(person.user_id)))
            is not MemberConnectStanding.CONNECTED
        ]
        return _Count(
            coverage=PodConnectorCoverage(
                eligible=len(eligible),
                connected=len(eligible) - len(waiting_on),
                waiting_on=waiting_on,
                truncated=truncated,
            ),
            waiting_on=waiting_on,
        )

    async def _context(self, user_id: UUID, pod_id: UUID) -> Context:
        if user_id not in self._contexts:
            self._contexts[user_id] = await self._member_context(
                self.uow, user_id=user_id, pod_id=pod_id
            )
        return self._contexts[user_id]

    # -- asking ---------------------------------------------------------------

    async def ask_members(
        self,
        ctx: Context,
        pod_id: UUID,
        slug: str,
        *,
        user_ids: list[UUID] | None = None,
    ) -> AskMembersResponse:
        """Ask the members a per-member connector is waiting on to connect.

        Each is asked once a day at most: an editor pressing the button twice,
        or two editors pressing it, asks nobody twice.
        """
        await ctx.require(Permissions.POD_CONNECTOR_MANAGE, ResourceRef.pod(pod_id))
        connector = await self.pod_connectors.get_by_slug(pod_id, slug)
        if connector is None:
            raise PodConnectorNotFoundError(slug)
        if connector.mode is not PodConnectorMode.PER_MEMBER:
            raise PodConnectorValidationError(
                "Only a connector that uses each person's own account asks "
                "members to connect.",
                reason="not_per_member",
            )
        editor = acting_person(ctx)
        if editor is None:
            raise PodConnectorValidationError(
                "Asking members is a person's act.", reason="requires_person"
            )
        people = await self._people(self.uow, pod_id, limit=MAX_POD_PEOPLE + 1)
        waiting_on = (await self._count(pod_id, connector, people)).waiting_on
        if user_ids is not None:
            chosen = set(user_ids)
            waiting_on = [one for one in waiting_on if one.user_id in chosen]
        link = connect_url(pod_id, connector.slug)
        title = await self._connector_title(connector.auth_config_id) or connector.slug
        text = f"This pod uses {title} — connect yours: {link}"
        day = datetime.now(UTC).date().isoformat()
        asked: list[MemberAwaited] = []
        recently: list[MemberAwaited] = []
        unreached: list[MemberAwaited] = []
        for member in waiting_on:
            claimed = await self._ledger.claim(
                pod_connector_id=connector.id, user_id=member.user_id
            )
            if claimed is None:
                unreached.append(member)
                continue
            if not claimed:
                recently.append(member)
                continue
            await self._tell(
                self.uow,
                pod_id=pod_id,
                user_id=member.user_id,
                asked_by=editor,
                text=text,
                key=f"connect-ask:{connector.id}:{member.user_id}:{day}",
            )
            asked.append(member)
        return AskMembersResponse(
            asked=asked,
            asked_recently=recently,
            not_asked=unreached,
            connect_url=link,
        )


def _counted(item: PodConnectorResponse) -> bool:
    return (
        item.required
        and item.mode is PodConnectorMode.PER_MEMBER
        and item.status is not PodConnectorStatus.UNAVAILABLE
    )


def _standing(status: str | None) -> MemberConnectStanding:
    if status is None:
        return MemberConnectStanding.MISSING
    if status == AccountStatus.CONNECTED.value:
        return MemberConnectStanding.CONNECTED
    # REAUTH_REQUIRED, and a DISCONNECTED account: either way its owner signs
    # in again rather than connecting a new one.
    return MemberConnectStanding.REAUTH_REQUIRED


__all__ = [
    "AskLedger",
    "MemberContext",
    "PodConnectorCoverageService",
    "PodPeople",
    "TellMember",
]
