"""Who is pushed a line outside the app -- planned as the line is posted.

A busy group read by push buries the questions in it, so a group seat hears
only what names or asks it until its member asks for more; somebody named or
asked who is not in the group is told too, when they may see it; a member
with the app open reads a line there and is not pushed it, unless it names
or asks them; and a line withheld from a member -- an answer made with
somebody else's access -- is never pushed to them in full.

Against Postgres and Redis (presence), through the API and the channels
contracts; a push is a delivery row naming the member (``recipient_user_id``).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.live.presence import mark_online
from app.modules.channels.contracts import (
    NewLine,
    NewQuestion,
    Principal,
    QuestionKind,
    Sender,
    post,
)
from app.modules.channels.contracts.deliveries import deliveries_of
from app.modules.channels.tests.e2e.channel_pod import Pod, group, say

pytestmark = pytest.mark.e2e


async def _pushed(message_id: str | UUID) -> set[str]:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        rows = await deliveries_of(uow, [UUID(str(message_id))])
    return {str(row.recipient_user_id) for row in rows if row.recipient_user_id}


def _naming(person: dict) -> list[dict[str, str]]:
    return [{"type": "mention", "kind": "MEMBER", "id": person["id"], "name": "x"}]


async def _hears_everything(pod: Pod, channel_id: str, who: dict) -> None:
    changed = await pod.client(who).patch(
        f"{pod.base}/{channel_id}/members/me", json={"notify": "ALL"}
    )
    assert changed.status_code == 200, changed.text


async def test_a_group_tells_its_members_only_what_names_them_until_they_ask(
    pod: Pod,
):
    crew = await group(pod, members=[pod.editor["id"], pod.user["id"]])

    plain = await say(pod, crew["id"], "Standup moved to ten.")
    named = await say(pod, crew["id"], "Can you check?", parts=_naming(pod.editor))
    await _hears_everything(pod, crew["id"], pod.user)
    later = await say(pod, crew["id"], "Thanks all.")

    assert await _pushed(plain["id"]) == set()
    assert await _pushed(named["id"]) == {pod.editor["id"]}
    assert await _pushed(later["id"]) == {pod.user["id"]}


async def test_somebody_named_or_asked_who_is_not_in_a_group_is_told(pod: Pod):
    """The viewer is not in the public group: naming them, or asking them,
    still reaches them -- they may read it."""
    square = await group(pod, title="Town square")

    named = await say(
        pod, square["id"], "What do you think?", parts=_naming(pod.viewer)
    )
    async with create_uow_from_session_maker(async_session_maker) as uow:
        asked = await post(
            uow,
            channel_id=UUID(square["id"]),
            line=NewLine(
                sender=Sender.agent(
                    UUID(pod.id), on_behalf_of=Principal.member(UUID(pod.owner["id"]))
                ),
                text="Approve the venue?",
            ),
            question=NewQuestion(
                kind=QuestionKind.ASK,
                ref=f"msg:{uuid4()}",
                question={"prompt": "Approve the venue?"},
                awaits=Principal.member(UUID(pod.viewer["id"])),
            ),
        )
        await uow.commit()

    assert asked is not None
    assert await _pushed(named["id"]) == {pod.viewer["id"]}
    assert await _pushed(asked.id) == {pod.viewer["id"]}


async def test_a_member_with_the_app_open_is_pushed_only_what_names_them(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"], pod.user["id"]])
    await _hears_everything(pod, crew["id"], pod.editor)
    await _hears_everything(pod, crew["id"], pod.user)
    await mark_online(UUID(pod.id), UUID(pod.editor["id"]))

    plain = await say(pod, crew["id"], "Lunch is here.")
    named = await say(pod, crew["id"], "Your slides?", parts=_naming(pod.editor))

    # The editor reads the plain line in the app; the user is away.
    assert await _pushed(plain["id"]) == {pod.user["id"]}
    assert await _pushed(named["id"]) == {pod.editor["id"], pod.user["id"]}


async def test_a_line_withheld_from_a_member_is_never_pushed_to_them(pod: Pod):
    """An answer the agent made with the owner's own access, in a group: the
    editor sees only that it was given -- and is not pushed what it said,
    even named in it."""
    crew = await group(pod, members=[pod.editor["id"]])
    await _hears_everything(pod, crew["id"], pod.editor)

    async with create_uow_from_session_maker(async_session_maker) as uow:
        answer = await post(
            uow,
            channel_id=UUID(crew["id"]),
            line=NewLine(
                sender=Sender.agent(
                    UUID(pod.id), on_behalf_of=Principal.member(UUID(pod.owner["id"]))
                ),
                text="Your salary review is on Friday.",
            ),
            mentions=[Principal.member(UUID(pod.editor["id"]))],
        )
        await uow.commit()

    assert answer is not None
    assert pod.editor["id"] not in await _pushed(answer.id)
