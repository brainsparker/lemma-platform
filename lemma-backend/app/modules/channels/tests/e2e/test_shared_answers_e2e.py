"""An agent's answer made with one member's access, shared with the channel.

Private by default: everybody else in the channel is shown that it was given,
for whom, and not what it said; the member it was made for is told so, and
may share it (``…/messages/{id}/share``). Once shared, it reads in full for
every member -- over REST, on the live socket, in search, and in the
background another member's run is handed -- and never for a contact.

Against Postgres through the API a member uses; the socket over a real
websocket.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.channels.contracts import NewLine, Principal, Sender, post
from app.modules.channels.contracts.backgrounds import (
    BackgroundRefs,
    BackgroundView,
    read_backgrounds,
    refs_of,
)
from app.modules.channels.contracts.lines import line_by_id
from app.modules.channels.domain.projection import Reader, may_read
from app.modules.channels.infrastructure.models import ChannelChangeModel
from app.modules.channels.tests.e2e.channel_pod import Pod, group, say
from app.modules.test_support.e2e.live_socket import live_socket
from app.modules.test_support.e2e.runtime import backend_server as _backend_server

pytestmark = [pytest.mark.e2e]

backend_server = _backend_server

SAID = "The Q3 margin is 41 percent"


async def _answer_for_owner(pod: Pod, channel_id: str) -> str:
    """The pod's agent answers the owner in the channel, with their access."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        line = await post(
            uow,
            channel_id=UUID(channel_id),
            line=NewLine(
                sender=Sender.agent(
                    UUID(pod.id),
                    on_behalf_of=Principal.member(UUID(pod.owner["id"])),
                    display_name="Bolt",
                ),
                text=SAID,
            ),
        )
        await uow.commit()
    assert line is not None
    return str(line.id)


def _line(page, line_id: str) -> dict:
    [found] = [item for item in page.json()["items"] if item["id"] == line_id]
    return found


async def _changes(channel_id: str) -> int:
    async with async_session_maker() as session:
        return int(
            await session.scalar(
                select(func.count()).where(
                    ChannelChangeModel.channel_id == UUID(channel_id)
                )
            )
            or 0
        )


async def test_the_asker_shares_an_answer_and_everybody_reads_it(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"], pod.user["id"]])
    answer = await _answer_for_owner(pod, crew["id"])
    lines = f"{pod.base}/{crew['id']}/messages"
    editor = pod.client(pod.editor)

    mine = _line(await pod.client().get(lines), answer)
    assert (mine["text"], mine["visibility"], mine["can_share"]) == (
        SAID,
        "asker_only",
        True,
    )
    theirs = _line(await editor.get(lines), answer)
    assert theirs["withheld"] and theirs["text"] is None
    assert (theirs["visibility"], theirs["can_share"]) == ("asker_only", False)
    search = await editor.get(f"/pods/{pod.id}/search", params={"q": "margin"})
    assert search.json()["items"] == []

    shared = await pod.client().post(f"{lines}/{answer}/share")
    assert shared.status_code == 200, shared.text
    body = shared.json()
    assert body["visibility"] == "channel" and body["shared_at"] is not None
    assert body["can_share"] is False

    theirs = _line(await editor.get(lines), answer)
    assert (theirs["withheld"], theirs["text"]) == (False, SAID)
    assert theirs["visibility"] == "channel"
    search = await editor.get(f"/pods/{pod.id}/search", params={"q": "margin"})
    assert [hit["message"]["id"] for hit in search.json()["items"]] == [answer]


async def test_only_the_member_it_was_made_for_may_share_it(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"]])
    answer = await _answer_for_owner(pod, crew["id"])
    mine = await say(pod, crew["id"], "a member's own line")
    lines = f"{pod.base}/{crew['id']}/messages"

    refused = await pod.client(pod.editor).post(f"{lines}/{answer}/share")
    assert refused.status_code == 403, refused.text
    not_an_answer = await pod.client().post(f"{lines}/{mine['id']}/share")
    assert not_an_answer.status_code == 403, not_an_answer.text
    # Somebody who cannot see the channel cannot even find the line.
    private = await group(pod, title="Owners", visibility="PRIVATE")
    hidden = await _answer_for_owner(pod, private["id"])
    outside = await pod.client(pod.user).post(
        f"{pod.base}/{private['id']}/messages/{hidden}/share"
    )
    assert outside.status_code == 404, outside.text

    still = _line(await pod.client(pod.editor).get(lines), answer)
    assert still["withheld"]


async def test_sharing_twice_changes_nothing(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"]])
    answer = await _answer_for_owner(pod, crew["id"])
    share = f"{pod.base}/{crew['id']}/messages/{answer}/share"

    first = await pod.client().post(share)
    changes = await _changes(crew["id"])
    again = await pod.client().post(share)

    assert again.status_code == 200, again.text
    assert again.json()["shared_at"] == first.json()["shared_at"]
    assert await _changes(crew["id"]) == changes, "a second share was a change"


async def test_a_shared_answer_reaches_another_members_run_but_no_contact(pod: Pod):
    crew = await group(pod, members=[pod.editor["id"]])
    answer = await _answer_for_owner(pod, crew["id"])
    async with create_uow_from_session_maker(async_session_maker) as uow:
        line = await line_by_id(uow, UUID(answer))
    assert line is not None
    refs = BackgroundRefs(
        pod_id=UUID(pod.id),
        channel_id=UUID(crew["id"]),
        view=BackgroundView.MEMBER_IN_LEMMA,
        user_id=UUID(pod.editor["id"]),
        items=refs_of([line]),
    )

    async def editors_run_reads() -> tuple[list[str], int]:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            [shown] = await read_backgrounds(uow, [refs])
        return [said.text for said in shown.lines], shown.withheld

    assert await editors_run_reads() == ([], 1)
    assert (
        await pod.client().post(f"{pod.base}/{crew['id']}/messages/{answer}/share")
    ).status_code == 200
    assert await editors_run_reads() == ([SAID], 0)

    async with create_uow_from_session_maker(async_session_maker) as uow:
        shared = await line_by_id(uow, UUID(answer))
    assert shared is not None and shared.shared_at is not None
    assert not may_read(Reader.contact(UUID(pod.id), source_id=None), shared)


async def test_a_shared_answer_reaches_other_members_on_the_socket(
    pod: Pod, backend_server
) -> None:
    crew = await group(pod, members=[pod.editor["id"]])
    answer = await _answer_for_owner(pod, crew["id"])
    async with live_socket(backend_server, pod.id, pod.editor["token"]) as editor:
        await editor.subscribe("c", "channel", channel_id=crew["id"])
        shared = await pod.client().post(
            f"{pod.base}/{crew['id']}/messages/{answer}/share"
        )
        assert shared.status_code == 200, shared.text
        edited = await editor.event("c", "line.edited", message__id=answer)
    assert edited["message"]["text"] == SAID
    assert not edited["message"]["withheld"]
    assert edited["message"]["visibility"] == "channel"
