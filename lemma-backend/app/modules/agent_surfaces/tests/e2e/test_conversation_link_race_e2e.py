"""One chat has one conversation link, even when a message is racing itself.

A direct chat has no channel and an email or Telegram chat may have no user id,
and a plain unique index never compares NULLs equal -- so for exactly those
chats nothing stopped two rapid first messages each inserting a link, after
which `get_by_external_thread` (which expects at most one row) failed on every
later message. `create` serialises on the chat and answers with the row that
won, so no schema change is needed.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.agent_surfaces.domain.entities import AgentSurfaceConversationLink
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurface,
    AgentSurfaceConversationLinkModel,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (
    SurfaceConversationLinkRepository,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_a_second_link_for_a_chat_with_no_channel_or_user_is_not_created(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    thread_id = uuid4().hex
    async with sessions() as session:
        surface = AgentSurface(
            pod_id=UUID(test_pod["id"]),
            organization_id=UUID(test_pod["organization_id"]),
            agent_id=UUID(test_pod["id"]),
            name=f"telegram-{uuid4().hex[:6]}",
            surface_type="TELEGRAM",
            event_mode="WEBHOOK",
            credential_mode="SYSTEM",
            config={},
        )
        session.add(surface)
        await session.flush()
        conversations = [
            ConversationModel(
                user_id=fixed_test_user["id"], pod_id=UUID(test_pod["id"])
            )
            for _ in range(2)
        ]
        session.add_all(conversations)
        await session.flush()
        surface_id = surface.id
        conversation_ids = [conversation.id for conversation in conversations]
        await session.commit()

    def link_for(conversation_id: UUID) -> AgentSurfaceConversationLink:
        return AgentSurfaceConversationLink(
            surface_id=surface_id,
            conversation_id=conversation_id,
            platform="TELEGRAM",
            external_channel_id=None,
            external_thread_id=thread_id,
            external_user_id=None,
            last_event={},
            last_inbound_at=datetime.now(timezone.utc),
        )

    async with sessions() as session:
        repository = SurfaceConversationLinkRepository(SqlAlchemyUnitOfWork(session))
        first = await repository.create(link_for(conversation_ids[0]))
        second = await repository.create(link_for(conversation_ids[1]))
        await session.commit()

    assert second.id == first.id
    assert second.conversation_id == conversation_ids[0], (
        "the loser of the race must be handed the link that stands"
    )
    async with sessions() as session:
        count = await session.scalar(
            select(func.count()).where(
                AgentSurfaceConversationLinkModel.surface_id == surface_id,
                AgentSurfaceConversationLinkModel.external_thread_id == thread_id,
            )
        )
        assert count == 1
        # And the lookup that used to raise on a duplicate answers.
        found = await SurfaceConversationLinkRepository(
            SqlAlchemyUnitOfWork(session)
        ).get_by_external_thread(
            surface_id=surface_id,
            platform="TELEGRAM",
            external_channel_id=None,
            external_thread_id=thread_id,
            external_user_id=None,
        )
        assert found is not None and found.id == first.id


async def test_a_burst_on_a_cold_dm_opens_one_conversation_not_one_each(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    """A quiet day, then two messages at once: one fresh conversation, both in it.

    The link exists but is past the reset window, so each message decides to
    open a fresh conversation. Only a missing link used to be locked, so both
    did, each repointed the link, and the person got two answers from two
    contexts with the first conversation orphaned.
    """
    from app.modules.agent_surfaces.composition import build_conversation_binder
    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
        ResolvedSurfaceUser,
    )
    from app.modules.agent_surfaces.services.agent_naming import (
        agent_name_for_agent_id,
    )
    from app.modules.agent_surfaces.services.surface_route_types import (
        ResolvedSurfaceRoute,
    )

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    chat_id = uuid4().hex
    async with sessions() as session:
        surface_row = AgentSurface(
            pod_id=pod_id,
            organization_id=UUID(test_pod["organization_id"]),
            agent_id=pod_id,
            name=f"telegram-{uuid4().hex[:6]}",
            surface_type="TELEGRAM",
            event_mode="WEBHOOK",
            credential_mode="SYSTEM",
            config={},
        )
        yesterdays = ConversationModel(user_id=user_id, pod_id=pod_id)
        session.add_all([surface_row, yesterdays])
        await session.flush()
        session.add(
            AgentSurfaceConversationLinkModel(
                surface_id=surface_row.id,
                conversation_id=yesterdays.id,
                platform="TELEGRAM",
                external_channel_id=chat_id,
                external_thread_id=chat_id,
                external_user_id="tg-1",
                routed_agent_id=pod_id,
                conversation_kind="DM",
                route_key="dm",
                last_event={},
                last_inbound_at=datetime.now(timezone.utc) - timedelta(hours=25),
            )
        )
        surface = surface_row.to_entity()
        agent_name = await agent_name_for_agent_id(
            SqlAlchemyUnitOfWork(session), pod_id
        )
        await session.commit()

    route = ResolvedSurfaceRoute(
        pod_id=pod_id,
        agent_id=pod_id,
        agent_name=agent_name,
        agent_display_name="Assistant",
        conversation_kind="DM",
        route_key="dm",
    )

    async def one_message(text: str) -> UUID:
        async with sessions() as session:
            link, _ = await build_conversation_binder(
                SqlAlchemyUnitOfWork(session)
            ).bind_conversation(
                surface=surface,
                parsed=ParsedInboundSurfaceEvent(
                    platform="TELEGRAM",
                    conversation_type=ConversationType.EXTERNAL_DM,
                    external_channel_id=chat_id,
                    external_thread_id=chat_id,
                    external_message_id=uuid4().hex,
                    sender_external_user_id="tg-1",
                    message_text=text,
                    is_dm=True,
                ),
                resolved_user=ResolvedSurfaceUser(
                    internal_user_id=user_id, external_user_id="tg-1"
                ),
                route=route,
            )
            await session.commit()
            return link.conversation_id

    first, second = await asyncio.gather(
        one_message("are you there?"), one_message("hello again")
    )

    assert first == second, "both messages must land in the one fresh conversation"
    assert first != yesterdays.id
    async with sessions() as session:
        opened = await session.scalar(
            select(func.count()).where(
                ConversationModel.pod_id == pod_id,
                ConversationModel.user_id == user_id,
                ConversationModel.id != yesterdays.id,
                ConversationModel.created_at
                >= datetime.now(timezone.utc) - timedelta(minutes=5),
                ConversationModel.conversation_metadata["external_thread_id"].astext
                == chat_id,
            )
        )
    assert opened == 1


async def test_repointing_a_link_another_writer_moved_first_changes_nothing(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    """The write compares the conversation it read; a stale read loses."""
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    pod_id = UUID(test_pod["id"])
    async with sessions() as session:
        surface = AgentSurface(
            pod_id=pod_id,
            organization_id=UUID(test_pod["organization_id"]),
            agent_id=pod_id,
            name=f"telegram-{uuid4().hex[:6]}",
            surface_type="TELEGRAM",
            event_mode="WEBHOOK",
            credential_mode="SYSTEM",
            config={},
        )
        current, stale, fresh = (
            ConversationModel(user_id=fixed_test_user["id"], pod_id=pod_id)
            for _ in range(3)
        )
        session.add_all([surface, current, stale, fresh])
        await session.flush()
        link = AgentSurfaceConversationLinkModel(
            surface_id=surface.id,
            conversation_id=current.id,
            platform="TELEGRAM",
            external_channel_id="c",
            external_thread_id=uuid4().hex,
            external_user_id="u",
            last_event={},
        )
        session.add(link)
        await session.commit()

    async with sessions() as session:
        repository = SurfaceConversationLinkRepository(SqlAlchemyUnitOfWork(session))
        lost = await repository.update_conversation(
            link_id=link.id,
            expected_conversation_id=stale.id,
            conversation_id=fresh.id,
            last_event={},
            last_message_id=None,
        )
        won = await repository.update_conversation(
            link_id=link.id,
            expected_conversation_id=current.id,
            conversation_id=fresh.id,
            last_event={},
            last_message_id=None,
        )
        await session.commit()

    assert lost is None
    assert won is not None and won.conversation_id == fresh.id


async def test_two_new_addresses_adopting_one_link_move_it_once(
    authenticated_client, db_session, test_pod, fixed_test_user
) -> None:
    """Each adopter holds the lock for its own new address, not for the link."""
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    pod_id = UUID(test_pod["id"])
    async with sessions() as session:
        surface = AgentSurface(
            pod_id=pod_id,
            organization_id=UUID(test_pod["organization_id"]),
            agent_id=pod_id,
            name=f"whatsapp-{uuid4().hex[:6]}",
            surface_type="WHATSAPP",
            event_mode="WEBHOOK",
            credential_mode="SYSTEM",
            config={},
        )
        conversation = ConversationModel(user_id=fixed_test_user["id"], pod_id=pod_id)
        session.add_all([surface, conversation])
        await session.flush()
        model = AgentSurfaceConversationLinkModel(
            surface_id=surface.id,
            conversation_id=conversation.id,
            platform="WHATSAPP",
            external_channel_id="pn-old",
            external_thread_id=f"{uuid4().hex}@pn-old",
            external_user_id="15550100",
            conversation_kind="DM",
            last_event={},
        )
        session.add(model)
        await session.commit()
        read = model.to_entity()

    async with sessions() as session:
        repository = SurfaceConversationLinkRepository(SqlAlchemyUnitOfWork(session))
        first = await repository.rebind_thread_address(
            link=read,
            surface_id=read.surface_id,
            external_channel_id="pn-a",
            external_thread_id="15550100@pn-a",
        )
        second = await repository.rebind_thread_address(
            link=read,
            surface_id=read.surface_id,
            external_channel_id="pn-b",
            external_thread_id="15550100@pn-b",
        )
        await session.commit()

    assert first is not None and first.external_channel_id == "pn-a"
    assert second is None, "the second adopter read an address that has moved"
