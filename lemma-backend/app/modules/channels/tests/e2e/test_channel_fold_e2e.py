"""Two contacts turn out to be one: their channels become the survivor's.

Everything that names the merged contact names the survivor afterwards --
what they said, the questions put to them and what they answered, where a line
was sent to them, the lines that mention them -- and their channel's sources,
lines and mentions move into the survivor's, which is the only one left.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import (
    ContactsHome,
    Place,
    Sender,
    SourceKind,
    post,
    source_for,
)
from app.modules.channels.infrastructure.models import (
    ChannelMentionModel,
    ChannelMessageDeliveryModel,
    ChannelMessageModel,
    ChannelModel,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact

pytestmark = pytest.mark.e2e


async def test_a_merged_contacts_channel_and_everything_naming_them_move_over(
    scenario, db_session: AsyncSession
):
    await scenario.create_org_with_pod(name_prefix="Channel fold")
    pod_id = UUID(scenario.pod_id)
    uow = SqlAlchemyUnitOfWork(db_session)
    people = []
    for number in ("5551001", "5551002"):
        contact = await open_contact(
            uow,
            pod_id=pod_id,
            kind=IdentityKind.TELEGRAM,
            value=number,
            strength=IdentityStrength.CHANNEL,
            display_name=None,
        )
        source = await source_for(
            uow,
            pod_id=pod_id,
            place=Place(
                surface_id=None,
                platform="TELEGRAM",
                kind=SourceKind.PRIVATE,
                external_channel_id=None,
                external_user_id=number,
                title=None,
            ),
            home=ContactsHome(contact.id),
        )
        await post(
            uow,
            channel_id=source.channel_id,
            sender=Sender.contact(contact.id),
            text=f"hello from {number}",
            source_id=source.id,
        )
        people.append((contact.id, source))
    (survivor, kept), (merged, folded) = people
    said = await db_session.scalar(
        select(ChannelMessageModel.id).where(
            ChannelMessageModel.sender_contact_id == merged
        )
    )
    assert said is not None
    await db_session.execute(
        update(ChannelMessageModel)
        .where(ChannelMessageModel.id == said)
        .values(
            interaction_kind="ASK",
            interaction_state="ANSWERED",
            awaits_contact_id=merged,
            answered_by_contact_id=merged,
            answered_at=datetime.now(timezone.utc),
        )
    )
    db_session.add_all(
        [
            ChannelMentionModel(
                message_id=said,
                channel_id=folded.channel_id,
                pod_id=pod_id,
                contact_id=merged,
            ),
            ChannelMessageDeliveryModel(
                message_id=said,
                pod_id=pod_id,
                platform="TELEGRAM",
                recipient_contact_id=merged,
                status="SENT",
            ),
        ]
    )
    await db_session.flush()

    await ChannelRepository(db_session).fold_contact(
        pod_id=pod_id, merged_id=merged, survivor_id=survivor
    )
    await db_session.commit()

    db_session.expire_all()
    assert await db_session.get(ChannelModel, folded.channel_id) is None
    line = await db_session.get(ChannelMessageModel, said)
    assert line is not None
    assert line.channel_id == kept.channel_id
    assert line.sender_contact_id == survivor
    assert line.awaits_contact_id == survivor
    assert line.answered_by_contact_id == survivor
    mention = await db_session.scalar(
        select(ChannelMentionModel).where(ChannelMentionModel.message_id == said)
    )
    assert mention is not None
    assert (mention.channel_id, mention.contact_id) == (kept.channel_id, survivor)
    delivery = await db_session.scalar(
        select(ChannelMessageDeliveryModel).where(
            ChannelMessageDeliveryModel.message_id == said
        )
    )
    assert delivery is not None and delivery.recipient_contact_id == survivor
