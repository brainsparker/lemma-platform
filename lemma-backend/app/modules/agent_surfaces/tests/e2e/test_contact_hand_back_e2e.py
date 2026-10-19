"""E2E for the hand-back window of a member assigned to a contact.

A member who writes to a contact holds them (``test_contact_care_e2e``); so
does one assigned to them, before writing a word: the window runs from being
assigned. Past it, the contact's next message is the agent's again, and the
member is told in their own channel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.tests.e2e.surface_work_support import direct_lines
from app.modules.agent_surfaces.tests.e2e.test_contact_care_e2e import (
    _contact_writes_on,
)
from app.modules.channels.infrastructure.models import ChannelModel

pytestmark = pytest.mark.e2e

HANDED_BACK = "the agent answers them again"


async def test_an_assigned_member_holds_the_contact_until_the_window_passes(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    pod_id = test_pod["id"]
    channel_id, write, runs = await _contact_writes_on(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    channel = f"/pods/{pod_id}/channels/{channel_id}"
    assigned = await authenticated_client.patch(
        channel, json={"assignee": fixed_test_user["id"], "hand_back_after_hours": 2}
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignee"]["id"] == fixed_test_user["id"]
    before = await runs()

    # Assigned, not yet written: the member holds them.
    await write("hold-2", "Is anybody looking at this?")
    assert await runs() == before
    assert (await authenticated_client.get(channel)).json()["assignee"] is not None

    async def told() -> bool:
        lines = await direct_lines(
            db_session, pod_id=pod_id, user_id=fixed_test_user["id"]
        )
        return any(HANDED_BACK in line.body for line in lines)

    assert not await told()

    # Assigned three hours ago, and silent since: past the window.
    await db_session.execute(
        update(ChannelModel)
        .where(ChannelModel.id == channel_id)
        .values(assigned_at=datetime.now(timezone.utc) - timedelta(hours=3))
    )
    await db_session.commit()
    await write("hold-3", "Hello?")

    assert await runs() == before + 1
    assert (await authenticated_client.get(channel)).json()["assignee"] is None
    assert await told()
