"""The link between a surface thread and the conversation behind it.

One lifecycle: place the thread on its channel source, find the conversation
the person has with this agent in that channel -- or open one -- and keep the
conversation's surface metadata in step.

A person has one conversation per agent in a channel, however many threads,
numbers or days they write across. That is what continues a private chat a
person starts afresh on Slack, or that arrives on another WhatsApp pool number:
the thread is new, the place and the person are not. Only a different agent,
or a contact's conversation that is no longer theirs, opens another.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import agent_display_name, effective_agent_id
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.channel_names import configured_channel_name
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    build_surface_event_metadata,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.channel_places import (
    ChannelSeats,
    ChannelSeatsInDatabase,
    home_of,
    place_of,
)
from app.modules.agent_surfaces.services.contact_keepers import owned_or_moved
from app.modules.agent_surfaces.services.surface_route_types import (
    ResolvedSurfaceRoute,
)
from app.modules.channels.contracts import Source

logger = get_logger(__name__)

_CONVERSATION_TITLE_MAX_LENGTH = 120


def same_agent(left: UUID | None, right: UUID | None, *, pod_id: UUID) -> bool:
    """Whether two agent references name the same agent.

    Compared through `effective_agent_id`, because the two sides are written in
    different eras and the assistant has more than one spelling. Raw, a
    conversation naming it by the pod's id and a route naming it by nobody
    differ, and this would read "the agent changed" for a thread whose agent
    never changed.
    """
    return effective_agent_id(left, pod_id=pod_id) == effective_agent_id(
        right, pod_id=pod_id
    )


def agent_changed(
    *,
    link: AgentSurfaceConversationLink,
    route: ResolvedSurfaceRoute,
    current_conversation_agent_id: UUID | None,
) -> bool:
    """Is the agent that answers now a different one from the conversation's?"""
    if current_conversation_agent_id is not None and not same_agent(
        current_conversation_agent_id, route.agent_id, pod_id=route.pod_id
    ):
        return True
    return not same_agent(link.routed_agent_id, route.agent_id, pod_id=route.pod_id)


class ConversationBinder:
    """Find or open the conversation a surface thread belongs to."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        surface_repository: SurfaceInstallationRepositoryPort,
        conversation_link_repository: SurfaceConversationLinkRepository,
        seats: ChannelSeats | None = None,
    ) -> None:
        self.uow = uow
        self.surface_repository = surface_repository
        self.conversation_link_repository = conversation_link_repository
        self.seats = seats or ChannelSeatsInDatabase(uow)

    async def bind_conversation(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        route: ResolvedSurfaceRoute,
        current_conversation_agent_id: UUID | None = None,
        contact_id: UUID | None = None,
    ) -> tuple[AgentSurfaceConversationLink, str | None]:
        """Return the link, plus the new conversation's title when one was created.

        ``contact_id`` binds one contact's conversation -- their private chat,
        or their questions in a group: the caller passes the member who looks
        after them as the user and the contact's link key as the external id,
        so each contact has a conversation of their own. A conversation that is
        no longer that contact's is left behind for a fresh one; one whose
        keeper changed moves to the new keeper with its history.

        The title is how a caller learns a *fresh* conversation started on this
        turn -- the only moment worth naming the thread on the platform. None
        means the conversation already existed.

        A resolved sender is a precondition: a conversation is kept by
        somebody, and `open_surface_conversation` takes a user id. Every caller
        refuses an unresolved sender well before here.
        """
        user_id = resolved_user.internal_user_id
        if user_id is None:
            raise ValueError("A conversation cannot be bound to an unresolved sender")
        external_user_id = resolved_user.external_user_id
        # Serialise on the chat before deciding anything, then read. Two
        # messages from one person arriving together would otherwise each
        # open a conversation of their own. The lock is held to commit, so the
        # loser reads the winner's link and continues it.
        await self.conversation_link_repository.lock_thread(
            surface_id=surface.id,
            platform=surface.surface_type.value,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=parsed.external_thread_id,
            external_user_id=external_user_id,
        )
        place = place_of(
            surface=surface,
            parsed=parsed,
            route=route,
            external_user_id=external_user_id,
        )
        source = await self.seats.source_for(
            pod_id=route.pod_id,
            place=place,
            home=home_of(place, user_id=user_id, contact_id=contact_id),
        )
        link = await self._link_at(surface, parsed, external_user_id)
        event_payload = parsed.model_dump(mode="json")
        if link is not None and await self._still_this_persons(
            link,
            user_id,
            contact_id=contact_id,
            route=route,
            current_conversation_agent_id=current_conversation_agent_id,
        ):
            updated = await self.conversation_link_repository.update_last_event(
                link_id=link.id,
                last_event=event_payload,
                last_message_id=parsed.external_message_id,
                source_id=source.id,
            )
            await self.seats.place_in_channel(
                conversation_id=link.conversation_id, channel_id=source.channel_id
            )
            await self._update_conversation_surface_metadata(
                conversation_id=link.conversation_id,
                surface=surface,
                parsed=parsed,
                external_user_id=external_user_id,
                route_key=link.route_key or route.route_key,
                routed_agent_id=link.routed_agent_id or route.agent_id,
                conversation_kind=link.conversation_kind or route.conversation_kind,
                agent_name=route.agent_name,
            )
            return (updated or link), None

        conversation_id, created_title = await self._persons_conversation(
            source=source,
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            user_id=user_id,
            contact_id=contact_id,
            route=route,
        )
        if link is not None:
            moved = await self.conversation_link_repository.update_conversation(
                link_id=link.id,
                expected_conversation_id=link.conversation_id,
                conversation_id=conversation_id,
                last_event=event_payload,
                last_message_id=parsed.external_message_id,
                routed_agent_id=route.agent_id,
                conversation_kind=route.conversation_kind,
                route_key=route.route_key,
                source_id=source.id,
            )
            if moved is not None:
                return moved, created_title
            # A writer that does not take the thread lock -- a notification
            # opening a conversation on this thread -- repointed it first.
            # Continue theirs rather than split one thread in two.
            logger.info(
                "agent_surfaces.conversation_binder.fresh_conversation_superseded.observed",
                surface_id=str(surface.id),
                conversation_id=str(conversation_id),
            )
            return (
                await self._link_at(surface, parsed, external_user_id) or link
            ), None
        created_link = await self.conversation_link_repository.create(
            AgentSurfaceConversationLink(
                surface_id=surface.id,
                conversation_id=conversation_id,
                source_id=source.id,
                platform=surface.surface_type.value,
                external_channel_id=parsed.external_channel_id,
                external_thread_id=parsed.external_thread_id,
                external_user_id=external_user_id,
                routed_agent_id=route.agent_id,
                conversation_kind=route.conversation_kind,
                route_key=route.route_key,
                last_event=event_payload,
                last_message_id=parsed.external_message_id,
                # This row exists because they just wrote to us.
                last_inbound_at=datetime.now(timezone.utc),
            ),
            # Locked and read above, before the conversation was found.
            locked=True,
        )
        if created_link.conversation_id != conversation_id:
            # Beaten to the insert despite the lock -- another writer does not
            # take it -- so the row that stands is theirs.
            return created_link, None
        return created_link, created_title

    async def _persons_conversation(
        self,
        *,
        source: Source,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        user_id: UUID,
        contact_id: UUID | None,
        route: ResolvedSurfaceRoute,
    ) -> tuple[UUID, str | None]:
        """The person's conversation with this agent in the channel, opened if new.

        A contact's found conversation must still be kept by ``user_id`` -- or
        move to them -- or one keeper's history would be handed to another.
        """
        found = await self.seats.conversation_in_channel(
            channel_id=source.channel_id,
            pod_id=route.pod_id,
            agent_id=route.agent_id,
            user_id=user_id,
            contact_id=contact_id,
        )
        if found is not None:
            conversation = await agent_conversations.surface_conversation(
                self.uow, found
            )
            if conversation is not None and await owned_or_moved(
                self.uow, conversation, user_id, contact_id=contact_id
            ):
                return found, None
        opened = await self._create_surface_conversation(
            user_id=user_id,
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            external_user_id=resolved_user.external_user_id,
            route=route,
            contact_id=contact_id,
            channel_id=source.channel_id,
        )
        return opened.id, opened.title

    async def _link_at(
        self,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        external_user_id: str | None,
    ) -> AgentSurfaceConversationLink | None:
        return await self.conversation_link_repository.get_by_external_thread(
            surface_id=surface.id,
            platform=surface.surface_type.value,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=parsed.external_thread_id,
            external_user_id=external_user_id,
        )

    async def _still_this_persons(
        self,
        link: AgentSurfaceConversationLink,
        user_id: UUID,
        *,
        contact_id: UUID | None,
        route: ResolvedSurfaceRoute,
        current_conversation_agent_id: UUID | None,
    ) -> bool:
        """Whether the linked conversation still answers this message.

        Its agent is still the one that answers, and -- for a contact -- it is
        still that contact's, kept by ``user_id``: one person's history is
        never another's.
        """
        if agent_changed(
            link=link,
            route=route,
            current_conversation_agent_id=current_conversation_agent_id,
        ):
            return False
        if contact_id is None:
            return True
        conversation = await agent_conversations.surface_conversation(
            self.uow, link.conversation_id
        )
        return (
            conversation is not None
            and conversation.audience.contact_id == contact_id
            and await owned_or_moved(
                self.uow, conversation, user_id, contact_id=contact_id
            )
        )

    async def _create_surface_conversation(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        user_id: UUID,
        external_user_id: str | None,
        route: ResolvedSurfaceRoute,
        contact_id: UUID | None = None,
        channel_id: UUID | None = None,
    ):
        surface_event_metadata = build_surface_event_metadata(
            surface.surface_type.value,
            parsed.metadata,
        )
        auth_ctx = await create_authorization_data_service(self.uow).build_user_context(
            user_id=user_id,
            pod_id=route.pod_id,
        )
        token = set_current_context(auth_ctx)
        try:
            return await agent_conversations.open_surface_conversation(
                self.uow,
                pod_id=route.pod_id,
                agent_name=route.agent_name,
                user_id=user_id,
                contact_id=contact_id,
                channel_id=channel_id,
                title=self._surface_conversation_title(
                    parsed,
                    fallback=f"{surface.surface_type.value} Conversation",
                ),
                metadata={
                    "source": "agent_surfaces",
                    "surface_id": str(surface.id),
                    "surface_platform": surface.surface_type.value,
                    "external_channel_id": parsed.external_channel_id,
                    "channel_name": configured_channel_name(surface, parsed),
                    "external_thread_id": parsed.external_thread_id,
                    "external_user_id": external_user_id,
                    "external_message_id": parsed.external_message_id,
                    "route_key": route.route_key,
                    "conversation_kind": route.conversation_kind,
                    "routed_agent_id": str(route.agent_id) if route.agent_id else None,
                    "agent_display_name": route.agent_display_name,
                    "surface_event_metadata": (
                        surface_event_metadata.model_dump(mode="json")
                        if surface_event_metadata
                        else None
                    ),
                },
            )
        finally:
            reset_current_context(token)

    async def _update_conversation_surface_metadata(
        self,
        *,
        conversation_id: UUID,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        external_user_id: str | None,
        route_key: str | None = None,
        routed_agent_id: UUID | None = None,
        conversation_kind: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        surface_event_metadata = build_surface_event_metadata(
            surface.surface_type.value,
            parsed.metadata,
        )
        updates = {
            "source": "agent_surfaces",
            "surface_id": str(surface.id),
            "surface_platform": surface.surface_type.value,
            "external_channel_id": parsed.external_channel_id,
            "channel_name": configured_channel_name(surface, parsed),
            "external_thread_id": parsed.external_thread_id,
            "external_user_id": external_user_id,
            "external_message_id": parsed.external_message_id,
            "route_key": route_key,
            "conversation_kind": conversation_kind,
            "routed_agent_id": str(routed_agent_id) if routed_agent_id else None,
            # From the route, which names the agent that is answering. Reading
            # it off the surface answered "whose installation is this", which is
            # a different question wherever the two differ -- and the reason the
            # personal path had to hand this function a doctored surface.
            "agent_display_name": agent_display_name(agent_name),
            "surface_event_metadata": (
                surface_event_metadata.model_dump(mode="json")
                if surface_event_metadata
                else None
            ),
        }
        await self.surface_repository.merge_conversation_metadata(
            conversation_id, updates
        )

    def _surface_conversation_title(
        self,
        parsed: ParsedInboundSurfaceEvent,
        *,
        fallback: str,
    ) -> str:
        title = " ".join((parsed.message_text or "").split())
        if not title:
            return fallback
        if len(title) <= _CONVERSATION_TITLE_MAX_LENGTH:
            return title
        return f"{title[: _CONVERSATION_TITLE_MAX_LENGTH - 3].rstrip()}..."
