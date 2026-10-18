"""Looking after contacts, end to end: forgetting, exporting, the cap, and loops.

Each test drives the real path from an inbound email (or an API call) to the
database, with only the model's next move scripted:

* forgetting a contact takes their rows, handles, conversations, web chat
  sessions and stored profile, and blanks what of theirs reached a member;
* exporting one hands over their rows too, a page at a time;
* past the contacts cap no run starts, the contact's channel waits on a
  person, the member is told where to look, and the contact hears once that a
  person will reply;
* a contact's run that needs a person calls one in with ``ask_a_person``: the
  channel waits on a person, and the member who looks after them is told;
* setting the cap is an owner's, and writing to a contact -- a line in their
  channel, carried to them afterwards -- an editor's, a few times a day at most;
* an auto-reply is never answered, and "STOP" unsubscribes -- a member's line
  to them after it stays in Lemma, recorded as not sent and why;
* a contact's history stays with them when the member looking after contacts
  changes, and a member leaving is told to the pod's admins.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.agent.contracts import contact_conversations
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    MessageModel,
)
from app.modules.agent_surfaces.composition import (
    build_surface_ingress,
    build_surface_turn_starter,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.contracts.direct_messages import ask_a_person
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurfaceExternalUser
from app.modules.contacts.infrastructure.visitor_sessions import (
    VisitorCodeModel,
    VisitorSessionModel,
)
from app.modules.agent_surfaces.services.contact_keepers import UNATTENDED_WARNING
from app.modules.agent_surfaces.tests.e2e.helpers import _resend_payload
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    record_model_requests,
    script_text,
    script_tool_call,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    contact_channel_id,
    deliver_member_posts,
    deliveries_of_line,
    direct_lines,
    member_writes,
)
from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import (
    CUSTOMER,
    _email_bot,
)
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact
from app.modules.contacts.infrastructure.models import (
    ContactIdentityModel,
    ContactModel,
)
from app.modules.datastore.contracts import contact_rows
from app.modules.channels.infrastructure.models import ChannelModel
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.usage.contracts.contacts_cap import contacts_cap_reached
from app.modules.usage.contracts.metering import check_run_budget
from app.modules.usage.domain.accounting import CONTACT_RUN
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e


async def _dana_writes(
    db_session: AsyncSession,
    address: str,
    message_id: str,
    text: str = "Where is my order?",
    *,
    in_reply_to: str | None = None,
) -> SurfaceChatContext:
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id=message_id,
                text=text,
                in_reply_to=f"<{in_reply_to}@resend-e2e.test>" if in_reply_to else None,
                references=[f"<{in_reply_to}@resend-e2e.test>"]
                if in_reply_to
                else None,
            ),
            headers={},
        ),
        script=[script_text("On its way.")],
    )
    assert isinstance(context, SurfaceChatContext)
    return context


async def _prepare(db_session: AsyncSession, payload: dict):
    """Ingress alone: what the webhook decides, before any run is driven."""
    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(
        SurfacePlatformWebhookIngress(source="resend", payload=payload, headers={})
    )
    await uow.commit()
    return context


async def _contact_owned_orders(client: AsyncClient, pod_id: str) -> None:
    created = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "orders",
            "primary_key_column": "id",
            "enable_rls": False,
            "contact_owned": True,
            "contact_columns": ["item"],
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "item", "type": "TEXT", "required": True},
            ],
        },
    )
    assert created.status_code == 201, created.text


async def _order(client: AsyncClient, pod_id: str, contact_id: str, item: str) -> None:
    created = await client.post(
        f"/pods/{pod_id}/datastore/tables/orders/records",
        json={"data": {"item": item, "contact_id": contact_id}},
    )
    assert created.status_code == 201, created.text


async def _dana(client: AsyncClient, pod_id: str) -> str:
    listed = await client.get(f"/pods/{pod_id}/contacts")
    assert listed.status_code == 200, listed.text
    [contact] = listed.json()["items"]
    return contact["id"]


async def _member(
    owner_client: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    pod_id: str,
    role: str,
    prefix: str,
) -> dict:
    user = await signup_user(async_client, prefix)
    org_member = await invite_org_member(
        owner_client, async_client, org_id=org_id, user=user
    )
    await add_pod_member(
        owner_client, pod_id=pod_id, organization_member_id=org_member["id"], role=role
    )
    return user


# ------------------------------------------------------------ forgetting


async def test_forgetting_a_contact_takes_everything_the_pod_held_about_them(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    owner = UUID(fixed_test_user["id"])
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    await _contact_owned_orders(authenticated_client, pod_id)
    context = await _dana_writes(db_session, address, "forget-1")
    dana = await _dana(authenticated_client, pod_id)
    await _order(authenticated_client, pod_id, dana, "Blue kettle")
    await _order(authenticated_client, pod_id, str(uuid4()), "Somebody else's")

    # A web chat session that named her, with a code -- and the bot called a
    # person in for her, which flags her channel and leaves a note there.
    widget = await authenticated_client.post(
        f"/pods/{pod_id}/web-widgets", json={"name": "Shop chat"}
    )
    assert widget.status_code == 201, widget.text
    now = datetime.now(timezone.utc)
    session_row = VisitorSessionModel(
        pod_id=UUID(pod_id),
        widget_id=UUID(widget.json()["id"]),
        secret_hash=uuid4().hex,
        contact_id=UUID(dana),
        strength="CODE",
        last_seen_at=now,
        expires_at=now + timedelta(days=30),
    )
    db_session.add(session_row)
    await db_session.flush()
    session_id = session_row.id
    db_session.add(
        VisitorCodeModel(
            session_id=session_id,
            email=CUSTOMER,
            code_hash=uuid4().hex,
            expires_at=now + timedelta(minutes=10),
            attempts=0,
        )
    )
    if (
        await db_session.scalar(
            select(AgentSurfaceExternalUser.id).where(
                AgentSurfaceExternalUser.platform == "RESEND",
                AgentSurfaceExternalUser.external_user_id == CUSTOMER,
            )
        )
    ) is None:
        db_session.add(
            AgentSurfaceExternalUser(
                platform="RESEND", external_user_id=CUSTOMER, raw_profile={}
            )
        )
    await db_session.commit()
    assert await ask_a_person(
        pod_id=UUID(pod_id),
        conversation_id=context.conversation_id,
        keeper_user_id=owner,
        reason="She wants to change her delivery address.",
    )
    her_channel = await db_session.scalar(
        select(ChannelModel.id).where(ChannelModel.contact_id == UUID(dana))
    )
    assert her_channel is not None

    forgotten = await authenticated_client.delete(f"/pods/{pod_id}/contacts/{dana}")
    assert forgotten.status_code == 204, forgotten.text

    db_session.expire_all()
    records = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/orders/records"
    )
    assert [row["item"] for row in records.json()["items"]] == ["Somebody else's"]
    assert (
        await db_session.scalar(
            select(ContactIdentityModel.id).where(
                ContactIdentityModel.contact_id == UUID(dana)
            )
        )
    ) is None
    assert await db_session.get(ConversationModel, context.conversation_id) is None
    assert await db_session.get(VisitorSessionModel, session_id) is None
    assert (
        await db_session.scalar(
            select(VisitorCodeModel.id).where(VisitorCodeModel.session_id == session_id)
        )
    ) is None
    assert (
        await db_session.scalar(
            select(AgentSurfaceExternalUser.id).where(
                AgentSurfaceExternalUser.platform == "RESEND",
                AgentSurfaceExternalUser.external_user_id == CUSTOMER,
            )
        )
    ) is None
    # Her channel goes with her: her lines, and the note calling a person in.
    assert await db_session.get(ChannelModel, her_channel) is None

    again = await authenticated_client.delete(f"/pods/{pod_id}/contacts/{dana}")
    assert again.status_code == 404


# ------------------------------------------------------------ listing


async def test_contacts_made_at_one_moment_page_without_one_lost(
    authenticated_client: AsyncClient, db_session: AsyncSession, test_pod
):
    pod_id = UUID(test_pod["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    for index in range(3):
        await open_contact(
            uow,
            pod_id=pod_id,
            kind=IdentityKind.EMAIL,
            value=f"customer{index}@client.example",
            strength=IdentityStrength.CHANNEL,
            display_name=None,
        )
    # One timestamp for all three: a cursor of the time alone would skip two.
    await db_session.execute(
        update(ContactModel)
        .where(ContactModel.pod_id == pod_id)
        .values(created_at=datetime(2026, 10, 1, 9, tzinfo=timezone.utc))
    )
    await db_session.commit()

    seen: list[str] = []
    before: str | None = None
    for _ in range(5):
        params: dict[str, str | int] = {"limit": 1}
        if before:
            params["before"] = before
        page = await authenticated_client.get(f"/pods/{pod_id}/contacts", params=params)
        assert page.status_code == 200, page.text
        seen += [item["id"] for item in page.json()["items"]]
        before = page.json().get("next_before")
        if not before:
            break

    assert len(seen) == 3 and len(set(seen)) == 3
    refused = await authenticated_client.get(
        f"/pods/{pod_id}/contacts", params={"before": "yesterday"}
    )
    assert refused.status_code == 400


# ------------------------------------------------------------ exporting


async def test_an_export_carries_their_rows_and_pages_through_everything(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    await _contact_owned_orders(authenticated_client, pod_id)
    # Two separate emails: one conversation, Dana's with the pod's agent.
    await _dana_writes(db_session, address, "export-1", "First question")
    await _dana_writes(db_session, address, "export-2", "Second question")
    dana = await _dana(authenticated_client, pod_id)
    for item in ("Blue kettle", "Teapot", "Saucer"):
        await _order(authenticated_client, pod_id, dana, item)
    await _order(authenticated_client, pod_id, str(uuid4()), "Somebody else's")
    # One of each to a page, so the export has to be followed to the end.
    monkeypatch.setattr(contact_conversations, "MAX_EXPORTED_CONVERSATIONS", 1)
    monkeypatch.setattr(contact_rows, "MAX_CONTACT_ROWS", 1)

    conversations, rows, pages = [], [], 0
    cursor: str | None = None
    while True:
        params = {"cursor": cursor} if cursor else {}
        page = await authenticated_client.get(
            f"/pods/{pod_id}/contacts/{dana}/export", params=params
        )
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["contact"]["id"] == dana
        conversations += body["conversations"]
        rows += body["rows"]
        pages += 1
        cursor = body.get("next_cursor")
        if not cursor:
            break
        assert pages < 10, "the export never ended"

    assert pages >= 3
    said = {m["text"] for c in conversations for m in c["messages"]}
    assert {"First question", "Second question"} <= said
    assert len(conversations) == 1
    assert sorted(row["values"]["item"] for row in rows) == [
        "Blue kettle",
        "Saucer",
        "Teapot",
    ]
    assert {row["table"] for row in rows} == {"orders"}

    refused = await authenticated_client.get(
        f"/pods/{pod_id}/contacts/{dana}/export", params={"cursor": "junk"}
    )
    assert refused.status_code == 400


# ------------------------------------------------------------ the contacts cap


async def test_past_the_cap_no_run_starts_and_a_person_takes_over(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    owner = UUID(fixed_test_user["id"])
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    first = await _dana_writes(db_session, address, "cap-1")
    capped = await authenticated_client.put(
        f"/usage/organizations/{fixed_test_org['id']}/contacts-cap",
        json={"monthly_limit_usd": 0},
    )
    assert capped.status_code == 200, capped.text
    sent_before = len(message_store.get_all("RESEND"))
    starter = build_surface_turn_starter(SessionUnitOfWorkFactory(async_session_maker))

    async def write(message_id: str, text: str) -> None:
        context = await _prepare(
            db_session,
            _resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id=message_id,
                text=text,
                in_reply_to="<cap-1@resend-e2e.test>",
                references=["<cap-1@resend-e2e.test>"],
            ),
        )
        assert isinstance(context, SurfaceChatContext)
        assert context.conversation_id == first.conversation_id
        await starter.execute_chat(context)

    runs_before = await _runs(db_session, first.conversation_id)
    await write("cap-2", "Hello again?")
    await write("cap-3", "Anyone there?")

    db_session.expire_all()
    # No run for either message, and both are in the conversation.
    assert await _runs(db_session, first.conversation_id) == runs_before
    texts = [
        (row.role, row.text)
        for row in (
            await db_session.scalars(
                select(MessageModel)
                .where(MessageModel.conversation_id == first.conversation_id)
                .order_by(MessageModel.sequence)
            )
        ).all()
    ]
    assert ("user", "Hello again?") in texts and ("user", "Anyone there?") in texts
    # Told once that a person will reply -- in the thread and by email.
    replies = [text for role, text in texts if text and "will reply here" in text]
    assert len(replies) == 1 and replies[0].startswith("A person from ")
    sent = message_store.get_all("RESEND")[sent_before:]
    assert len(sent) == 1 and "will reply here" in str(sent[0])
    # The member was told once, in their own channel, where to look -- not
    # what she wrote: her words stay in her channel, which forgetting her takes.
    [note] = await direct_lines(db_session, pod_id=pod_id, user_id=owner)
    assert note.members_only and note.interaction_kind is None
    assert "Somebody outside the space is waiting for you" in note.body
    assert "Hello again?" not in note.body and "Anyone there?" not in note.body
    # Both are on Dana's channel, which waits on a person to answer by hand.
    conversation = await db_session.get(ConversationModel, first.conversation_id)
    assert conversation is not None and conversation.channel_id is not None
    channel = f"/pods/{pod_id}/channels/{conversation.channel_id}"
    shown = await authenticated_client.get(channel)
    assert shown.status_code == 200, shown.text
    assert (
        shown.json()["attention"]["reason"] == "The monthly cap on answering contacts"
    )
    timeline = await authenticated_client.get(f"{channel}/messages")
    heard = [
        line["text"]
        for line in timeline.json()["items"]
        if line["sender"]["kind"] == "CONTACT"
    ]
    assert {"Hello again?", "Anyone there?"} <= set(heard)


async def test_a_member_who_steps_in_answers_alone_until_they_hand_back(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """Taking a contact's channel quiets its agents; releasing it wakes them."""
    pod_id = test_pod["id"]
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    first = await _dana_writes(db_session, address, "step-1")
    conversation = await db_session.get(ConversationModel, first.conversation_id)
    assert conversation is not None and conversation.channel_id is not None
    channel = f"/pods/{pod_id}/channels/{conversation.channel_id}"
    starter = build_surface_turn_starter(SessionUnitOfWorkFactory(async_session_maker))

    async def write(message_id: str, text: str) -> None:
        context = await _prepare(
            db_session,
            _resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id=message_id,
                text=text,
                in_reply_to="<step-1@resend-e2e.test>",
                references=["<step-1@resend-e2e.test>"],
            ),
        )
        assert isinstance(context, SurfaceChatContext)
        await starter.execute_chat(context)

    taken = await authenticated_client.put(f"{channel}/handler")
    assert taken.status_code == 200, taken.text
    assert taken.json()["handler"]["id"] == fixed_test_user["id"]
    runs_before = await _runs(db_session, first.conversation_id)

    await write("step-2", "Can a person look at this?")

    db_session.expire_all()
    assert await _runs(db_session, first.conversation_id) == runs_before

    released = await authenticated_client.delete(f"{channel}/handler")
    assert released.status_code == 200, released.text
    assert released.json()["handler"] is None

    await write("step-3", "Thanks, back to the bot.")

    db_session.expire_all()
    assert await _runs(db_session, first.conversation_id) == runs_before + 1


async def test_a_contacts_run_calls_a_person_in_and_the_keeper_is_told_where(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """What a contact needs from a person is asked with ``ask_a_person``.

    It used to be a notification to the member, with the contact's question
    quoted and an answer carried back through an approval card. Now the
    contact's channel waits on a person -- in Contacts, for every member who
    sees them -- with a note there to members only, and the member who looks
    after them is told where to look. Stepping in answers the call. A contact's
    run has nothing else that reaches a member: no ``channel_post``.
    """
    pod_id = test_pod["id"]
    owner = UUID(fixed_test_user["id"])
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    seen = record_model_requests(monkeypatch)
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id="person-1",
                text="Can I get a refund on order 1182?",
            ),
            headers={},
        ),
        script=[
            script_tool_call(
                "ask_a_person",
                {"reason": "A refund on order 1182 is a member's call."},
                tool_call_id="call-1",
            ),
            script_text("Someone from the team will reply here."),
        ],
    )
    assert isinstance(context, SurfaceChatContext)

    assert seen, "the contact's run asked the model nothing"
    assert all("ask_a_person" in request["tools"] for request in seen)
    assert all("channel_post" not in request["tools"] for request in seen)
    conversation = await db_session.get(ConversationModel, context.conversation_id)
    assert conversation is not None and conversation.channel_id is not None
    channel = f"/pods/{pod_id}/channels/{conversation.channel_id}"
    shown = (await authenticated_client.get(channel)).json()
    assert shown["kind"] == "CONTACT"
    assert shown["attention"]["reason"] == "A refund on order 1182 is a member's call."
    timeline = (await authenticated_client.get(f"{channel}/messages")).json()["items"]
    [called] = [line for line in timeline if line["members_only"]]
    assert (
        called["text"]
        == "This needs a person: A refund on order 1182 is a member's call."
    )
    [notice] = await direct_lines(db_session, pod_id=pod_id, user_id=owner)
    assert notice.members_only and notice.interaction_kind is None
    assert "A contact you look after needs a person" in notice.body

    taken = await authenticated_client.put(f"{channel}/handler")
    assert taken.status_code == 200, taken.text
    assert (await authenticated_client.get(channel)).json()["attention"] is None


async def _runs(db_session: AsyncSession, conversation_id: UUID) -> int:
    return len(
        (
            await db_session.scalars(
                select(AgentRunModel.id).where(
                    AgentRunModel.conversation_id == conversation_id
                )
            )
        ).all()
    )


async def test_the_default_cap_holds_until_an_owner_removes_it(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_org,
    fixed_test_user,
):
    org_id = UUID(fixed_test_org["id"])
    db_session.add(
        UsageRecord(
            user_id=UUID(fixed_test_user["id"]),
            organization_id=org_id,
            occurred_at=datetime.now(timezone.utc),
            source_type=CONTACT_RUN,
            profile_id="contacts",
            profile_scope="SYSTEM",
            model_name="m",
            cost_amount=Decimal("60"),
        )
    )
    await db_session.commit()

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        assert await contacts_cap_reached(uow, organization_id=org_id)
        # The pre-run check every outside run makes refuses it too.
        with pytest.raises(UsageLimitExceededError):
            await check_run_budget(
                factory=SessionUnitOfWorkFactory(async_session_maker),
                organization_id=org_id,
                user_id=UUID(fixed_test_user["id"]),
                profile_scope="SYSTEM",
                outside_audience=True,
            )

    removed = await authenticated_client.put(
        f"/usage/organizations/{org_id}/contacts-cap",
        json={"monthly_limit_usd": None},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["spent_this_month_usd"] == 60

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        assert not await contacts_cap_reached(uow, organization_id=org_id)


async def test_only_an_owner_sets_the_contacts_cap(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]
    editor = await signup_user(async_client, "cap-editor")
    invite = await authenticated_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": editor["email"], "role": "ORG_EDITOR"},
    )
    assert invite.status_code == 201, invite.text
    accepted = await async_client.post(
        f"/organizations/invitations/{invite.json()['id']}/accept",
        headers=auth_headers(editor),
    )
    assert accepted.status_code == 200, accepted.text

    read = await async_client.get(
        f"/usage/organizations/{org_id}/contacts-cap", headers=auth_headers(editor)
    )
    assert read.status_code == 200, read.text
    refused = await async_client.put(
        f"/usage/organizations/{org_id}/contacts-cap",
        json={"monthly_limit_usd": None},
        headers=auth_headers(editor),
    )
    assert refused.status_code == 403, refused.text
    unchanged = await authenticated_client.get(
        f"/usage/organizations/{org_id}/contacts-cap"
    )
    assert unchanged.json()["is_default"] is True


# ------------------------------------------------------------ follow-ups


@pytest.mark.drives_work
async def test_writing_to_a_contact_takes_an_editor_and_a_daily_limit(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """A member writes to a contact in the contact's channel; the line goes out
    afterwards, by ``surface.member_post``. Past the day's limit the line is
    still the member's, in Lemma -- recorded as not sent, and why -- rather
    than the post being refused."""
    pod_id = test_pod["id"]
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    await _dana_writes(db_session, address, "follow-limit-1")
    dana = await _dana(authenticated_client, pod_id)
    channel = await contact_channel_id(authenticated_client, pod_id, dana)
    user = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_USER",
        prefix="follow-user",
    )

    # A pod user neither sees the contact nor speaks for the pod to them.
    as_user = auth_headers(user)
    unseen = await async_client.get(f"/pods/{pod_id}/contacts/{dana}", headers=as_user)
    assert unseen.status_code == 403, unseen.text
    theirs = await async_client.get(
        f"/pods/{pod_id}/channels", params={"kind": "CONTACT"}, headers=as_user
    )
    assert theirs.json()["items"] == []
    refused = await async_client.post(
        f"/pods/{pod_id}/channels/{channel}/messages",
        json={"text": "Hello from me"},
        headers=as_user,
    )
    assert refused.status_code == 404, refused.text

    monkeypatch.setattr(
        surface_settings, "surface_contact_follow_ups_per_contact_per_day", 1
    )
    sent_before = len(message_store.get_all("RESEND"))
    written = await member_writes(
        authenticated_client, pod_id, channel, "Your order shipped today."
    )
    assert written["sender"]["kind"] == "MEMBER"
    assert written["sender"]["id"] == fixed_test_user["id"]
    await deliver_member_posts()
    [delivery] = await deliveries_of_line(written["id"])
    assert (delivery.platform, delivery.status.value) == ("RESEND", "SENT")
    mails = message_store.get_all("RESEND")[sent_before:]
    assert len(mails) == 1 and "Your order shipped today." in str(mails[0])

    limited = await member_writes(
        authenticated_client, pod_id, channel, "And another thing."
    )
    await deliver_member_posts()
    [held] = await deliveries_of_line(limited["id"])
    assert held.status.value == "SKIPPED"
    assert held.error == "That is as many messages as one contact gets in a day"
    assert len(message_store.get_all("RESEND")) == sent_before + 1


async def test_a_follow_up_the_platform_refused_is_never_shown_as_said(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    context = await _dana_writes(db_session, address, "not-sent-1")
    uow = SqlAlchemyUnitOfWork(db_session)
    await contact_conversations.append_follow_up(
        uow,
        conversation_id=context.conversation_id,
        message="This one never left.",
        sent_by_user_id=UUID(fixed_test_user["id"]),
        delivered=False,
    )
    await contact_conversations.append_follow_up(
        uow,
        conversation_id=context.conversation_id,
        message="This one did.",
        sent_by_user_id=UUID(fixed_test_user["id"]),
        delivered=True,
    )
    await db_session.commit()

    shown = await contact_conversations.visible_messages(
        uow, context.conversation_id, after=-1, limit=100
    )

    texts = [message.text for message in shown]
    assert "This one did." in texts
    assert "This one never left." not in texts


# ------------------------------------------------------------ loops and STOP


async def test_an_auto_reply_is_neither_answered_nor_made_a_contact(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    before = len(message_store.get_all("RESEND"))
    payload = _resend_payload(
        sender_email=CUSTOMER,
        assistant_address=address,
        message_id="ooo-1",
        text="I am out of the office until Monday.",
        subject="Automatic reply: Prices",
    )
    payload["headers"]["auto-submitted"] = "auto-replied"

    assert await _prepare(db_session, payload) is None
    assert len(message_store.get_all("RESEND")) == before
    listed = await authenticated_client.get(f"/pods/{pod_id}/contacts")
    assert listed.json()["items"] == []


@pytest.mark.drives_work
async def test_stop_unsubscribes_the_address_it_came_from(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    await _dana_writes(db_session, address, "stop-1")
    dana = await _dana(authenticated_client, pod_id)

    stopped = await _prepare(
        db_session,
        _resend_payload(
            sender_email=CUSTOMER,
            assistant_address=address,
            message_id="stop-2",
            text="STOP",
        ),
    )

    assert stopped is None
    db_session.expire_all()
    identity = await db_session.scalar(
        select(ContactIdentityModel).where(
            ContactIdentityModel.contact_id == UUID(dana)
        )
    )
    assert identity is not None and identity.unsubscribed_at is not None
    # A member's line to her afterwards stays in Lemma, recorded as not sent.
    sent_before = len(message_store.get_all("RESEND"))
    channel = await contact_channel_id(authenticated_client, pod_id, dana)
    written = await member_writes(
        authenticated_client, pod_id, channel, "One more thing."
    )
    await deliver_member_posts()
    [held] = await deliveries_of_line(written["id"])
    assert held.status.value == "SKIPPED"
    assert held.error == "The contact asked not to be written to there"
    assert len(message_store.get_all("RESEND")) == sent_before


# ------------------------------------------------------------ who looks after them


@pytest.mark.drives_work
async def test_a_contacts_history_moves_with_them_to_a_new_keeper(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    first = await _dana_writes(db_session, address, "keeper-1", "Order 1182?")
    # Contacts' chats are an editor's or an admin's to see, so a member who
    # only uses the pod cannot be the one they belong to.
    user = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_USER",
        prefix="user",
    )
    refused = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/{surface['name']}",
        json={
            "config": {"contacts": {"answer": "anyone", "looked_after_by": user["id"]}}
        },
    )
    assert refused.status_code in (400, 422), refused.text
    assert "editor or admin" in refused.text
    keeper = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_EDITOR",
        prefix="keeper",
    )
    moved = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/{surface['name']}",
        json={
            "config": {
                "contacts": {"answer": "anyone", "looked_after_by": keeper["id"]}
            }
        },
    )
    assert moved.status_code == 200, moved.text

    again = await _dana_writes(
        db_session, address, "keeper-2", "Any news?", in_reply_to="keeper-1"
    )

    assert again.conversation_id == first.conversation_id
    assert again.user_id == UUID(keeper["id"])
    db_session.expire_all()
    conversation = await db_session.get(ConversationModel, first.conversation_id)
    assert conversation is not None and conversation.user_id == UUID(keeper["id"])

    # The keeper leaves: the admins hear of it, and the bot's settings say so.
    members = await authenticated_client.get(f"/pods/{pod_id}/members")
    assert members.status_code == 200, members.text
    keeper_member = next(
        item
        for item in members.json()["items"]
        if str(item.get("user_id")) == keeper["id"]
    )
    removed = await authenticated_client.delete(
        f"/pods/{pod_id}/members/{keeper_member['pod_member_id']}"
    )
    assert removed.status_code == 204, removed.text
    # The removal wrote the surfaces' `surfaces.member_removed` item.
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])
    shown = await authenticated_client.get(f"/pods/{pod_id}/surfaces/{surface['name']}")
    assert shown.json()["contacts_warning"] == UNATTENDED_WARNING
    told = await direct_lines(db_session, pod_id=pod_id, user_id=fixed_test_user["id"])
    [note] = [line for line in told if "has left the space" in line.body]
    # A note to them alone, in their own channel: nothing waits on an answer.
    assert note.members_only and note.interaction_kind is None
