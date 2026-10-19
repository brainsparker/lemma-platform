"""E2E for what the hourly push budget holds back, and what it never does.

A member who has had ``PushBudget``'s worth of pushes from a pod this hour is
not pushed again: the line waits in Lemma, and the held lines of a channel
reach them later as one note that names them (``push_digests``). What names
them or asks them something is never held. A plain line in a group is not
worth an email. Against a real database, real Redis and the fake mail server.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.redis.client import get_redis
from app.modules.agent_surfaces.events.work_kinds import SURFACE_PUSH_DIGEST
from app.modules.agent_surfaces.services.notification_delivery import (
    UndeliverableReason,
)
from app.modules.agent_surfaces.services.notification_rate_limiter import (
    DEFAULT_MAX_PER_RECIPIENT_PER_HOUR,
)
from app.modules.agent_surfaces.services.push_digests import (
    DIGESTED,
    HELD_FOR_DIGEST,
    PushDigest,
    send_digest,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    deliveries_of_line,
    direct_lines,
    member_writes,
)
from app.modules.agent_surfaces.tests.e2e.test_channel_deliveries_e2e import (
    _colleague,
    _deliver,
    _due_now,
    _resend_bot,
    _resend_unreachable,
    _row,
    _tell,
)
from app.modules.channels.contracts import DeliveryStatus
from app.modules.channels.infrastructure.models import ChannelMemberModel

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


def _budget_key(pod_id: str, user_id: str) -> str:
    return f"notify:rate:{pod_id}:{user_id}"


async def _spend_their_budget(pod_id: str, user_id: str) -> None:
    """They have had the hour's pushes from this pod already."""
    await get_redis().set(
        _budget_key(pod_id, user_id), DEFAULT_MAX_PER_RECIPIENT_PER_HOUR, ex=3600
    )


async def _charged(pod_id: str, user_id: str) -> int:
    return int(await get_redis().get(_budget_key(pod_id, user_id)) or 0)


async def _digests_queued(user_id: str) -> int:
    async with async_session_maker() as session:
        return int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM work_items WHERE kind = :kind "
                    "AND payload->>'recipient_user_id' = :user_id"
                ),
                {"kind": SURFACE_PUSH_DIGEST, "user_id": user_id},
            )
            or 0
        )


async def _a_group_with(
    client: AsyncClient, pod_id: str, colleague: dict, *, notify: str = "ALL"
) -> str:
    created = await client.post(
        f"/pods/{pod_id}/channels",
        json={"title": "Launch", "members": [colleague["id"]]},
    )
    assert created.status_code == 201, created.text
    channel_id = created.json()["id"]
    async with async_session_maker() as session:
        await session.execute(
            update(ChannelMemberModel)
            .where(
                ChannelMemberModel.channel_id == UUID(channel_id),
                ChannelMemberModel.user_id == UUID(colleague["id"]),
            )
            .values(notify=notify)
        )
        await session.commit()
    return channel_id


async def _push_to(line_id: str, user_id: str):
    [push] = [
        delivery
        for delivery in await deliveries_of_line(line_id)
        if delivery.recipient_user_id == UUID(user_id)
    ]
    return push


@pytest.fixture
async def setting(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
):
    """A pod with a mailbox, and a colleague in it to be pushed to."""
    pod_id = test_pod["id"]
    await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        api_base=fake_resend.api_base,
    )
    colleague = await _colleague(
        authenticated_client, async_client, org_id=fixed_test_org["id"], pod_id=pod_id
    )
    return pod_id, colleague


async def test_a_line_naming_them_is_pushed_however_many_they_had(
    authenticated_client, setting, message_store
):
    """The budget keeps room for exactly this: a line that names them goes,
    even after the hour's plain pushes are spent -- by email, from a group."""
    pod_id, colleague = setting
    group = await _a_group_with(authenticated_client, pod_id, colleague)
    await _spend_their_budget(pod_id, colleague["id"])

    named = await member_writes(
        authenticated_client,
        pod_id,
        group,
        "Can you take the release notes?",
        parts=[{"type": "mention", "kind": "MEMBER", "id": colleague["id"]}],
    )
    push = await _push_to(named["id"], colleague["id"])
    await _deliver(push.id)

    assert (await _row(push.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert mail["to"] == [colleague["email"]]


async def test_a_plain_group_line_is_not_emailed(
    authenticated_client, setting, message_store
):
    """No chat app reaches them, and the line does not name them: it waits in
    Lemma rather than spending the pod's emails on a group's chatter."""
    pod_id, colleague = setting
    group = await _a_group_with(authenticated_client, pod_id, colleague)

    plain = await member_writes(authenticated_client, pod_id, group, "Lunch at one.")
    push = await _push_to(plain["id"], colleague["id"])
    await _deliver(push.id)

    skipped = await _row(push.id)
    assert skipped.status is DeliveryStatus.SKIPPED
    assert skipped.reason == UndeliverableReason.NOT_WORTH_AN_EMAIL
    assert message_store.get_all("RESEND") == []


async def test_held_pushes_come_back_as_one_note_that_names_them(
    authenticated_client, db_session, setting, message_store
):
    """Two plain lines held by the budget: one digest queued for them, which
    tells them once -- a note naming them, pushed despite the budget -- and
    then has nothing left to tell."""
    pod_id, colleague = setting
    await _spend_their_budget(pod_id, colleague["id"])
    first = await _tell(authenticated_client, pod_id, colleague["email"], text="One.")
    second = await _tell(authenticated_client, pod_id, colleague["email"], text="Two.")
    held = [
        await _push_to(first["message_id"], colleague["id"]),
        await _push_to(second["message_id"], colleague["id"]),
    ]
    for push in held:
        await _deliver(push.id)
        row = await _row(push.id)
        assert (row.status, row.reason) == (DeliveryStatus.SKIPPED, HELD_FOR_DIGEST)
    assert message_store.get_all("RESEND") == []
    assert await _digests_queued(colleague["id"]) == 1

    digest = PushDigest(
        pod_id=UUID(pod_id),
        recipient_user_id=UUID(colleague["id"]),
        channel_id=UUID(first["channel_id"]),
    )
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        assert await send_digest(uow, digest) == 2
        await uow.commit()
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        assert await send_digest(uow, digest) == 0
        await uow.commit()

    for push in held:
        assert (await _row(push.id)).reason == DIGESTED
    lines = await direct_lines(db_session, pod_id=pod_id, user_id=colleague["id"])
    [note] = [line for line in lines if "2 lines" in line.body]
    note_push = await _push_to(str(note.id), colleague["id"])
    await _deliver(note_push.id)
    assert (await _row(note_push.id)).status is DeliveryStatus.SENT
    [mail] = message_store.get_all("RESEND")
    assert "waiting in Lemma" in mail["text"]


async def test_a_push_is_charged_to_the_budget_once_however_often_it_is_tried(
    authenticated_client, db_session, setting, fixed_test_user
):
    pod_id, colleague = setting
    await _resend_unreachable(db_session, fixed_test_user)
    told = await _tell(authenticated_client, pod_id, colleague["email"])
    push = await _push_to(told["message_id"], colleague["id"])

    await _deliver(push.id)
    assert (await _row(push.id)).status is DeliveryStatus.FAILED
    charged = await _charged(pod_id, colleague["id"])
    await _due_now(push.id)
    await _deliver(push.id)

    assert (await _row(push.id)).attempts == 2
    assert await _charged(pod_id, colleague["id"]) == charged == 1
