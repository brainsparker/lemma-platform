"""The channel app, end to end: a channel made in Lemma, its threads, who is
named, what is new for whom, what is waiting on whom, and search.

Three members: the owner, an editor and a user. The owner makes a channel and
adds the editor; everybody in the pod sees a group, members of it hear of it.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import (
    ContactsHome,
    InteractionKind,
    NewQuestion,
    Place,
    Principal,
    Sender,
    SourceKind,
    post,
    source_for,
)
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = pytest.mark.e2e


@pytest.fixture
async def pod(scenario):
    await scenario.create_org_with_pod(name_prefix="Channel app")
    editor = await scenario.create_user("app-editor")
    await scenario.add_user_to_pod(user=editor, role="POD_EDITOR")
    user = await scenario.create_user("app-user")
    await scenario.add_user_to_pod(user=user, role="POD_USER")

    class Pod:
        pass

    p = Pod()
    p.id, p.scenario = scenario.pod_id, scenario
    p.owner = scenario.owner_user
    p.editor, p.user = editor, user
    p.owner_client, p.client = scenario.owner_client, scenario.async_client
    return p


def _base(pod) -> str:
    return f"/pods/{pod.id}/channels"


async def _channel(pod, **body) -> dict:
    made = await pod.owner_client.post(
        _base(pod),
        json={"title": "launch", "members": [pod.editor["id"]], **body},
    )
    assert made.status_code == 201, made.text
    return made.json()


async def _say(pod, channel_id: str, text: str, *, as_user=None, **body) -> dict:
    client, headers = (
        (pod.owner_client, {})
        if as_user is None
        else (pod.client, auth_headers(as_user))
    )
    said = await client.post(
        f"{_base(pod)}/{channel_id}/messages",
        json={"text": text, **body},
        headers=headers,
    )
    assert said.status_code == 201, said.text
    return said.json()


async def test_a_channel_made_in_lemma_is_every_members_and_its_members_hear_of_it(
    pod,
):
    channel = await _channel(pod, topic="Shipping the beta")
    assert channel["kind"] == "GROUP" and channel["topic"] == "Shipping the beta"
    assert {p["person"]["id"] for p in channel["participants"]} == {
        pod.owner["id"],
        pod.editor["id"],
    }
    assert channel["me"]["joined"]

    await _say(pod, channel["id"], "kickoff at 3")

    as_editor = auth_headers(pod.editor)
    listed = (await pod.client.get(_base(pod), headers=as_editor)).json()["items"]
    [row] = [c for c in listed if c["id"] == channel["id"]]
    assert row["unread"]["lines"] == 1
    assert row["last_line"]["text"] == "kickoff at 3"
    assert row["last_line"]["sender"]["id"] == pod.owner["id"]
    # A member not in it sees the group, with nothing counted as new for them.
    others = (await pod.client.get(_base(pod), headers=auth_headers(pod.user))).json()[
        "items"
    ]
    [theirs] = [c for c in others if c["id"] == channel["id"]]
    assert theirs["unread"]["lines"] == 0

    # Reading it clears it -- for the reader alone.
    read = await pod.client.post(
        f"{_base(pod)}/{channel['id']}/read", headers=as_editor
    )
    assert read.status_code == 204, read.text
    listed = (await pod.client.get(_base(pod), headers=as_editor)).json()["items"]
    assert [c for c in listed if c["id"] == channel["id"]][0]["unread"]["lines"] == 0


async def test_a_thread_hangs_from_its_line_and_stays_out_of_the_timeline(pod):
    channel = await _channel(pod)
    root = await _say(pod, channel["id"], "who has the release notes?")
    await _say(
        pod, channel["id"], "I do", as_user=pod.editor, thread_root_id=root["id"]
    )
    reply = await _say(pod, channel["id"], "thanks", thread_root_id=root["id"])
    # A reply to a reply joins the same thread.
    deeper = await _say(pod, channel["id"], "np", thread_root_id=reply["id"])
    assert deeper["thread_root_id"] == root["id"]

    timeline = (
        await pod.owner_client.get(f"{_base(pod)}/{channel['id']}/messages")
    ).json()["items"]
    assert [line["text"] for line in timeline] == ["who has the release notes?"]
    assert timeline[0]["reply_count"] == 3
    thread = (
        await pod.owner_client.get(
            f"{_base(pod)}/{channel['id']}/messages/{root['id']}/thread"
        )
    ).json()["items"]
    assert [line["text"] for line in thread] == ["I do", "thanks", "np"]


async def test_naming_somebody_counts_for_them_until_they_read_it(pod):
    channel = await _channel(pod)
    await _say(
        pod,
        channel["id"],
        "@user can you check the copy?",
        mentions=[{"kind": "MEMBER", "id": pod.user["id"]}],
    )
    as_user = auth_headers(pod.user)
    inbox = (await pod.client.get(f"/pods/{pod.id}/inbox", headers=as_user)).json()
    assert inbox["totals"]["mentions"] == 1
    listed = (await pod.client.get(_base(pod), headers=as_user)).json()["items"]
    [row] = [c for c in listed if c["id"] == channel["id"]]
    # Named, so in it now: the line is new to them, and names them.
    assert row["unread"] == {"lines": 1, "mentions": 1, "waiting": 0}

    await pod.client.post(f"{_base(pod)}/{channel['id']}/read", headers=as_user)
    inbox = (await pod.client.get(f"/pods/{pod.id}/inbox", headers=as_user)).json()
    assert inbox["totals"]["mentions"] == 0


async def test_only_who_wrote_a_line_changes_it_and_a_deleted_line_keeps_its_place(
    pod,
):
    channel = await _channel(pod)
    line = await _say(pod, channel["id"], "draft at 5")
    path = f"{_base(pod)}/{channel['id']}/messages/{line['id']}"

    refused = await pod.client.patch(
        path, json={"text": "mine now"}, headers=auth_headers(pod.editor)
    )
    assert refused.status_code == 403, refused.text
    edited = await pod.owner_client.patch(path, json={"text": "draft at 6"})
    assert edited.json()["text"] == "draft at 6" and edited.json()["edited_at"]

    deleted = await pod.owner_client.delete(path)
    assert deleted.status_code == 204, deleted.text
    timeline = (
        await pod.owner_client.get(f"{_base(pod)}/{channel['id']}/messages")
    ).json()["items"]
    assert [(entry["deleted"], entry["text"]) for entry in timeline] == [(True, None)]


async def test_a_question_waits_on_one_person_and_only_they_answer_it(
    pod, db_session: AsyncSession
):
    channel = await _channel(pod)
    asked = await post(
        SqlAlchemyUnitOfWork(db_session),
        channel_id=UUID(channel["id"]),
        sender=Sender.agent(
            UUID(pod.id),
            on_behalf_of=Principal.member(UUID(pod.owner["id"])),
            display_name="Lem",
        ),
        text="Ship the beta today?",
        question=NewQuestion(
            kind=InteractionKind.ASK,
            question={
                "ref": "note:beta",
                "prompt": "Ship today?",
                "choices": ["yes", "no"],
            },
            awaits=Principal.member(UUID(pod.editor["id"])),
        ),
    )
    await db_session.commit()
    assert asked is not None

    as_editor = auth_headers(pod.editor)
    inbox = (await pod.client.get(f"/pods/{pod.id}/inbox", headers=as_editor)).json()
    assert inbox["totals"]["waiting"] == 1
    [waiting] = inbox["waiting"]
    assert waiting["message"]["interaction"]["question"] == {
        "prompt": "Ship today?",
        "choices": ["yes", "no"],
    }
    assert waiting["message"]["interaction"]["awaits"]["id"] == pod.editor["id"]

    answer = f"{_base(pod)}/{channel['id']}/messages/{asked.id}/answer"
    refused = await pod.owner_client.post(answer, json={"answer": {"choice": "yes"}})
    assert refused.status_code == 403, refused.text
    answered = await pod.client.post(
        answer, json={"answer": {"choice": "yes"}}, headers=as_editor
    )
    assert answered.status_code == 200, answered.text
    interaction = answered.json()["interaction"]
    assert interaction["state"] == "ANSWERED"
    assert interaction["answer"] == {"choice": "yes"}
    assert interaction["answered_by"]["id"] == pod.editor["id"]

    again = await pod.client.post(
        answer, json={"answer": {"choice": "no"}}, headers=as_editor
    )
    assert again.status_code == 409, again.text
    inbox = (await pod.client.get(f"/pods/{pod.id}/inbox", headers=as_editor)).json()
    assert inbox["totals"]["waiting"] == 0 and inbox["waiting"] == []


async def test_archiving_and_who_may_change_a_channel(pod):
    channel = await _channel(pod)
    path = f"{_base(pod)}/{channel['id']}"
    refused = await pod.client.patch(
        path, json={"title": "mine"}, headers=auth_headers(pod.user)
    )
    assert refused.status_code == 403, refused.text

    archived = await pod.owner_client.patch(path, json={"archived": True})
    assert archived.json()["archived"] is True
    live = (await pod.owner_client.get(_base(pod))).json()["items"]
    assert channel["id"] not in {c["id"] for c in live}
    folded = (await pod.owner_client.get(_base(pod), params={"archived": True})).json()[
        "items"
    ]
    assert channel["id"] in {c["id"] for c in folded}
    closed = await pod.owner_client.post(f"{path}/messages", json={"text": "anyone?"})
    assert closed.status_code == 409, closed.text


async def test_how_a_member_hears_of_a_channel_is_theirs(pod):
    channel = await _channel(pod)
    path = f"{_base(pod)}/{channel['id']}/me"
    set_ = await pod.client.put(
        path,
        json={"notify": "MENTIONS", "notify_via": ["telegram"]},
        headers=auth_headers(pod.editor),
    )
    assert set_.status_code == 200, set_.text
    assert set_.json()["me"] == {
        "notify": "MENTIONS",
        "notify_via": ["TELEGRAM"],
        "last_read_at": None,
        "joined": True,
    }
    mine = (await pod.owner_client.get(f"{_base(pod)}/{channel['id']}")).json()["me"]
    assert mine["notify"] == "ALL"


async def test_search_finds_lines_in_the_channels_the_reader_sees(pod):
    channel = await _channel(pod)
    await _say(pod, channel["id"], "the invoice for acme is late")
    found = (
        await pod.owner_client.get(f"{_base(pod)}/search", params={"q": "acme invoice"})
    ).json()["items"]
    assert [hit["message"]["text"] for hit in found] == ["the invoice for acme is late"]


async def test_writing_in_a_contacts_channel_reaches_them_and_steps_in(
    pod, db_session: AsyncSession
):
    uow = SqlAlchemyUnitOfWork(db_session)
    contact = await open_contact(
        uow,
        pod_id=UUID(pod.id),
        kind=IdentityKind.TELEGRAM,
        value="5551234",
        strength=IdentityStrength.CHANNEL,
        display_name="Ravi",
    )
    source = await source_for(
        uow,
        pod_id=UUID(pod.id),
        place=Place(
            surface_id=None,
            platform="TELEGRAM",
            kind=SourceKind.PRIVATE,
            external_channel_id=None,
            external_user_id="5551234",
            title=None,
        ),
        home=ContactsHome(contact.id),
    )
    await post(
        uow,
        channel_id=source.channel_id,
        sender=Sender.contact(contact.id),
        text="Is my order in?",
        source_id=source.id,
    )
    await db_session.commit()
    path = f"{_base(pod)}/{source.channel_id}/messages"

    refused = await pod.client.post(
        path, json={"text": "hi"}, headers=auth_headers(pod.user)
    )
    assert refused.status_code == 404, refused.text  # a user does not see contacts
    written = await pod.client.post(
        path, json={"text": "Yes, it ships today."}, headers=auth_headers(pod.editor)
    )
    assert written.status_code == 201, written.text
    # It goes where Ravi wrote from, so the run answering him reads it.
    assert written.json()["source_id"] == str(source.id)
    channel = (
        await pod.client.get(
            f"{_base(pod)}/{source.channel_id}", headers=auth_headers(pod.editor)
        )
    ).json()
    assert channel["handler"]["id"] == pod.editor["id"]
    assert channel["person"]["name"] == "Ravi"

    note = await pod.client.post(
        path,
        json={"text": "careful, he is upset", "members_only": True},
        headers=auth_headers(pod.editor),
    )
    assert note.json()["source_id"] is None and note.json()["members_only"]
