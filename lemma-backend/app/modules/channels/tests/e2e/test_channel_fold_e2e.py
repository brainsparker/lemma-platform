"""Two contacts turn out to be one: their channels become the survivor's.

Everything that names the merged contact names the survivor afterwards --
what they said, the questions put to them, the lines that mention them, their
seat -- and their channel's sources, lines, questions and mentions move into
the survivor's, numbered on after the survivor's own lines. The merged
channel is gone.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import (
    ContactsHome,
    NewLine,
    NewQuestion,
    Place,
    Principal,
    QuestionKind,
    Sender,
    SourceKind,
    post,
    source_for,
)
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMentionModel,
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
    ChannelSourceModel,
)
from app.modules.channels.infrastructure.repository import ChannelRepository
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact

pytestmark = pytest.mark.e2e


async def test_a_merged_contacts_channel_and_everything_naming_them_move_over(
    scenario, db_session: AsyncSession
):
    await scenario.create_org_with_pod(name_prefix="Channel fold")
    pod_id = UUID(scenario.pod_id)
    owner = UUID(scenario.owner_user["id"])
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
                account_key="bot-1",
                kind=SourceKind.PRIVATE,
                external_channel_id=None,
                external_user_id=number,
            ),
            home=ContactsHome(contact.id),
        )
        await post(
            uow,
            channel_id=source.channel_id,
            line=NewLine(
                sender=Sender.contact(contact.id),
                text=f"hello from {number}",
                source_id=source.id,
            ),
        )
        people.append((contact.id, source))
    (survivor, kept), (merged, folded) = people
    asked = await post(
        uow,
        channel_id=folded.channel_id,
        line=NewLine(sender=Sender.member(owner), text="Which order?"),
        question=NewQuestion(
            kind=QuestionKind.ASK,
            ref="e2e:fold",
            question={"prompt": "Which order?"},
            awaits=Principal.contact(merged),
        ),
        mentions=[Principal.contact(merged)],
    )
    assert asked is not None
    await db_session.commit()

    await ChannelRepository(db_session).fold_contact(
        pod_id=pod_id, merged_id=merged, survivor_id=survivor
    )
    await db_session.commit()

    db_session.expire_all()
    assert await db_session.get(ChannelModel, folded.channel_id) is None
    lines = (
        await db_session.execute(
            select(ChannelMessageModel.seq, ChannelMessageModel.body)
            .where(ChannelMessageModel.channel_id == kept.channel_id)
            .order_by(ChannelMessageModel.seq)
        )
    ).all()
    assert [tuple(line) for line in lines] == [
        (1, "hello from 5551001"),
        (2, "hello from 5551002"),
        (3, "Which order?"),
    ]
    head = await db_session.get(ChannelModel, kept.channel_id)
    assert head is not None and head.last_seq == 3
    said = await db_session.scalar(
        select(ChannelMessageModel).where(
            ChannelMessageModel.body == "hello from 5551002"
        )
    )
    assert said is not None and said.sender_contact_id == survivor
    question = await db_session.get(ChannelQuestionModel, asked.id)
    assert question is not None
    assert (question.channel_id, question.awaits_contact_id) == (
        kept.channel_id,
        survivor,
    )
    mention = await db_session.scalar(
        select(ChannelMentionModel).where(ChannelMentionModel.message_id == asked.id)
    )
    assert mention is not None
    assert (mention.channel_id, mention.contact_id) == (kept.channel_id, survivor)
    source = await db_session.get(ChannelSourceModel, folded.id)
    assert source is not None and source.channel_id == kept.channel_id
    seats = (
        await db_session.scalars(
            select(ChannelMemberModel.contact_id).where(
                ChannelMemberModel.contact_id.in_([survivor, merged])
            )
        )
    ).all()
    assert seats == [survivor]
