"""The channels API, as a pod's members use it -- end to end.

A member reads every group's channel and only their own direct one: another
member's is missing, exactly as a channel that does not exist. A contact's
channel is an editor's or an admin's to read; to anybody else it is missing too. A contact's channel can be answered by hand -- by a
member who may write to contacts -- and nothing else can. Pages walk back
through a timeline, and through the list, without skipping or repeating.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import (
    ContactsHome,
    GroupHome,
    MembersHome,
    Place,
    Principal,
    Sender,
    Source,
    SourceKind,
    post,
    source_for,
)
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = pytest.mark.e2e


async def _place(
    db_session: AsyncSession, pod_id: str, *, where: str, home, kind=SourceKind.PRIVATE
) -> Source:
    """A source heard for the first time, with its channel made for ``home``."""
    return await source_for(
        SqlAlchemyUnitOfWork(db_session),
        pod_id=UUID(pod_id),
        place=Place(
            surface_id=None,
            platform="TELEGRAM",
            kind=kind,
            external_channel_id=where if kind is SourceKind.SHARED else None,
            external_user_id=None if kind is SourceKind.SHARED else where,
            title="Launch crew" if kind is SourceKind.SHARED else None,
        ),
        home=home,
    )


async def _say(db_session: AsyncSession, source: Source, text: str, sender: Sender):
    await post(
        SqlAlchemyUnitOfWork(db_session),
        channel_id=source.channel_id,
        sender=sender,
        text=text,
        source_id=source.id,
    )


@pytest.fixture
async def pod(scenario, db_session: AsyncSession):
    """A pod with an owner, an editor, a viewer and a user, and one channel of
    each kind."""
    await scenario.create_org_with_pod(name_prefix="Channels API")
    editor = await scenario.create_user("channels-editor")
    await scenario.add_user_to_pod(user=editor, role="POD_EDITOR")
    viewer = await scenario.create_user("channels-viewer")
    await scenario.add_user_to_pod(user=viewer, role="POD_VIEWER")
    user = await scenario.create_user("channels-user")
    await scenario.add_user_to_pod(user=user, role="POD_USER")
    owner = UUID(scenario.owner_user["id"])
    contact = await open_contact(
        SqlAlchemyUnitOfWork(db_session),
        pod_id=UUID(scenario.pod_id),
        kind=IdentityKind.TELEGRAM,
        value="5550001",
        strength=IdentityStrength.CHANNEL,
        display_name="Dana",
    )
    direct = await _place(
        db_session, scenario.pod_id, where="owner-tg", home=MembersHome(owner)
    )
    dana = await _place(
        db_session, scenario.pod_id, where="5550001", home=ContactsHome(contact.id)
    )
    group = await _place(
        db_session,
        scenario.pod_id,
        where="-100",
        home=GroupHome(title="Launch crew"),
        kind=SourceKind.SHARED,
    )
    await _say(db_session, direct, "remind me at 5", Sender.member(owner))
    await _say(db_session, dana, "Where is my order?", Sender.contact(contact.id))
    for n in range(5):
        await _say(db_session, group, f"line {n}", Sender.member(owner))
    await db_session.commit()

    class Pod:
        pass

    p = Pod()
    p.id, p.scenario = scenario.pod_id, scenario
    p.editor, p.viewer, p.user = editor, viewer, user
    p.contact_id = contact.id
    p.direct, p.dana, p.group = direct, dana, group
    return p


def _base(pod) -> str:
    return f"/pods/{pod.id}/channels"


async def test_another_members_direct_channel_is_missing_to_them(pod):
    owner, editor = pod.scenario.owner_client, pod.scenario.async_client
    as_editor = auth_headers(pod.editor)
    direct = f"{_base(pod)}/{pod.direct.channel_id}"

    mine = (await owner.get(_base(pod))).json()["items"]
    theirs = (await editor.get(_base(pod), headers=as_editor)).json()["items"]

    assert {c["kind"] for c in mine} == {"DIRECT", "CONTACT", "GROUP"}
    assert {c["kind"] for c in theirs} == {"CONTACT", "GROUP"}
    assert (await owner.get(direct)).status_code == 200
    for path in (direct, f"{direct}/messages"):
        refused = await editor.get(path, headers=as_editor)
        assert refused.status_code == 404, refused.text
        assert "remind me" not in refused.text


async def test_contacts_and_their_channels_are_for_editors_and_admins(pod):
    """Who wrote to the pod, and what they said, is not every member's: a
    member who only uses the pod, or views it, sees the groups they talk in
    but no contact, and a contact's channel reads to them as missing."""
    client = pod.scenario.async_client
    dana = f"{_base(pod)}/{pod.dana.channel_id}"
    contacts = f"/pods/{pod.id}/contacts"

    for member in (pod.user, pod.viewer):
        headers = auth_headers(member)
        listed = await client.get(_base(pod), headers=headers)
        assert listed.status_code == 200, listed.text
        assert {c["kind"] for c in listed.json()["items"]} == {"GROUP"}
        only_contacts = await client.get(
            _base(pod), params={"kind": "CONTACT"}, headers=headers
        )
        assert only_contacts.json()["items"] == []
        for path in (dana, f"{dana}/messages"):
            refused = await client.get(path, headers=headers)
            assert refused.status_code == 404, refused.text
            assert "Where is my order" not in refused.text
        marked = await client.post(f"{dana}/read", headers=headers)
        assert marked.status_code == 404, marked.text
        for path in (contacts, f"{contacts}/{pod.contact_id}"):
            refused = await client.get(path, headers=headers)
            assert refused.status_code == 403, refused.text
            assert "Dana" not in refused.text

    as_editor = auth_headers(pod.editor)
    seen = await client.get(f"{dana}/messages", headers=as_editor)
    assert seen.status_code == 200, seen.text
    assert [m["text"] for m in seen.json()["items"]] == ["Where is my order?"]
    people = await client.get(contacts, headers=as_editor)
    assert [c["display_name"] for c in people.json()["items"]] == ["Dana"]


async def test_an_answer_made_with_a_members_own_access_is_theirs_to_read(
    pod, db_session: AsyncSession
):
    """In a group, the bot answers a member with that member's own access. The
    timeline shows everybody else that it answered them, and not what it said:
    they may not be able to see what the answer was made from."""
    owner = UUID(pod.scenario.owner_user["id"])
    agent_id = UUID(pod.id)  # the pod's own assistant has the pod's id
    await post(
        SqlAlchemyUnitOfWork(db_session),
        channel_id=pod.group.channel_id,
        sender=Sender.agent(
            agent_id, on_behalf_of=Principal.member(owner), display_name="Lem"
        ),
        text="Your Q3 revenue was 1.2M",
        source_id=pod.group.id,
    )
    await db_session.commit()
    messages = f"{_base(pod)}/{pod.group.channel_id}/messages"

    mine = (await pod.scenario.owner_client.get(messages)).json()["items"][-1]
    theirs = (
        await pod.scenario.async_client.get(messages, headers=auth_headers(pod.editor))
    ).json()["items"][-1]

    assert mine["text"] == "Your Q3 revenue was 1.2M" and not mine["withheld"]
    assert theirs["withheld"] and theirs["text"] is None
    for line in (mine, theirs):
        assert line["sender"] == {"kind": "AGENT", "id": str(agent_id), "name": "Lem"}
        assert line["on_behalf_of"] == {
            "kind": "MEMBER",
            "id": str(owner),
            "name": None,
        }


async def test_a_contacts_channel_is_answered_by_hand_by_who_may_write_to_contacts(
    pod,
):
    client = pod.scenario.async_client
    handler = f"{_base(pod)}/{pod.dana.channel_id}/handler"

    viewer = await client.put(handler, headers=auth_headers(pod.viewer))
    assert viewer.status_code == 403, viewer.text

    taken = await client.put(handler, headers=auth_headers(pod.editor))
    assert taken.status_code == 200, taken.text
    assert taken.json()["handler"] == {
        "kind": "MEMBER",
        "id": pod.editor["id"],
        "name": None,
    }

    released = await client.delete(handler, headers=auth_headers(pod.editor))
    assert released.json()["handler"] is None


async def test_only_a_contacts_channel_is_answered_by_hand(pod):
    owner = pod.scenario.owner_client
    for channel in (pod.group, pod.direct):
        refused = await owner.put(f"{_base(pod)}/{channel.channel_id}/handler")
        assert refused.status_code == 409, refused.text


async def test_a_timeline_pages_back_without_skipping_or_repeating(pod):
    owner = pod.scenario.owner_client
    messages = f"{_base(pod)}/{pod.group.channel_id}/messages"

    seen: list[str] = []
    before = None
    while True:
        page = await owner.get(
            messages, params={"limit": 2, **({"before": before} if before else {})}
        )
        assert page.status_code == 200, page.text
        body = page.json()
        seen = [line["text"] for line in body["items"]] + seen
        before = body["next_before"]
        if not before:
            break

    assert seen == [f"line {n}" for n in range(5)]
    junk = await owner.get(messages, params={"before": "yesterday"})
    assert junk.status_code == 400


async def test_the_list_pages_without_skipping_or_repeating(pod):
    owner = pod.scenario.owner_client
    seen: list[str] = []
    before = None
    while True:
        page = await owner.get(
            _base(pod), params={"limit": 1, **({"before": before} if before else {})}
        )
        assert page.status_code == 200, page.text
        seen += [channel["id"] for channel in page.json()["items"]]
        before = page.json()["next_before"]
        if not before:
            break

    assert sorted(seen) == sorted(
        str(source.channel_id) for source in (pod.direct, pod.dana, pod.group)
    )


async def test_reading_a_channel_marks_the_reader_alone(pod):
    """Including a member who only ever reads it, and has said nothing there."""
    client = pod.scenario.async_client
    channel = f"{_base(pod)}/{pod.group.channel_id}"

    marked = await client.post(f"{channel}/read", headers=auth_headers(pod.editor))
    assert marked.status_code == 204, marked.text

    participants = (await pod.scenario.owner_client.get(channel)).json()["participants"]
    reads = {
        p["person"]["id"]: p["last_read_at"]
        for p in participants
        if p["person"]["kind"] == "MEMBER"
    }
    assert reads[pod.editor["id"]] is not None
    assert reads[pod.scenario.owner_user["id"]] is None


async def test_a_channel_of_another_pod_is_missing(pod, scenario):
    other = await scenario.create_pod(name_prefix="Elsewhere")
    stray = await pod.scenario.owner_client.get(
        f"/pods/{other['id']}/channels/{pod.dana.channel_id}"
    )
    assert stray.status_code == 404, stray.text
