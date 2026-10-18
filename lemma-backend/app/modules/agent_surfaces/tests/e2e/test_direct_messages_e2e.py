"""E2E for reaching a member: a line in their own channel, carried to them.

What an agent or a member has to tell somebody in the pod is a line in that
person's DIRECT channel (``services.direct_messages``). The line is there
whatever happens next; then it is carried to where they are -- the chat app
they last used, or email -- unless they turned the channel's notifications
off. Every attempt is recorded on the line.

So these assert the line first and the delivery second, against a real
database and the real surface service: a pod with nothing connected still
holds the line, and says why it went no further; a pod with a mailbox
cold-opens an email thread whose reply finds its way back; a platform the
agent names is honoured or refused, never swapped; a retried send with the
same key is the same line; and nobody outside the pod is ever a recipient.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.capabilities.open_questions import open_question_text
from app.modules.agent_surfaces.composition import build_direct_messages
from app.modules.agent_surfaces.contracts.direct_messages import (
    send_direct_message,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.services.direct_messages import DirectMessage
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    deliveries_of_line,
    direct_lines,
)
from app.modules.channels.infrastructure.models import ChannelModel
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

#: Why a line went no further, when the pod has nothing to carry it on.
NO_SURFACE = "The pod has no active surface to reach anyone on."


async def _tell(client: AsyncClient, pod_id: str, to: str, **overrides) -> dict:
    payload = {"to": to, "text": "What did you ship yesterday?"} | overrides
    response = await client.post(f"/pods/{pod_id}/notices", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _resend_bot(
    client: AsyncClient, db_session: AsyncSession, *, pod_id: str, user, fake_resend
) -> str:
    """The pod's mailbox; its address."""
    from app.modules.agent_surfaces.tests.e2e.helpers import (
        _create_surface,
        _ensure_connector_account,
    )
    from app.modules.connectors.domain.connector import AuthProvider

    account = await _ensure_connector_account(
        db_session,
        user_id=user["id"],
        connector_id="resend",
        credentials={"api_key": "resend-token", "api_base_url": fake_resend.api_base},
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    surface = await _create_surface(
        client, pod_id, config={"type": "RESEND", "account_id": str(account.id)}
    )
    address = surface.get("surface_identity_email")
    if not address:
        row = await db_session.get(AgentSurface, UUID(surface["id"]))
        assert row is not None
        address = row.surface_identity_email
    assert address
    return address


async def _colleague(
    owner_client: AsyncClient, async_client: AsyncClient, *, org_id: str, pod_id: str
) -> dict:
    user = await signup_user(async_client, "colleague")
    member = await invite_org_member(
        owner_client, async_client, org_id=org_id, user=user
    )
    await add_pod_member(
        owner_client,
        pod_id=pod_id,
        organization_member_id=member["id"],
        role="POD_EDITOR",
    )
    return user


async def _pod_surfaces(db_session: AsyncSession, *pod_ids: str) -> list[AgentSurface]:
    await db_session.commit()
    return list(
        (
            await db_session.execute(
                select(AgentSurface).where(
                    AgentSurface.pod_id.in_([UUID(pod_id) for pod_id in pod_ids])
                )
            )
        )
        .scalars()
        .all()
    )


async def test_a_line_is_in_their_channel_even_when_nothing_can_carry_it(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """No surface and no mailbox to mint: not a failure. The line is in their
    channel, new to them, and the reason it went no further is on it."""
    pod_id = test_pod["id"]

    told = await _tell(authenticated_client, pod_id, fixed_test_user["email"])

    assert told["delivered_via"] is None
    assert told["reason"]
    [line] = await direct_lines(
        db_session, pod_id=pod_id, user_id=fixed_test_user["id"]
    )
    assert str(line.id) == told["message_id"]
    assert line.body == "What did you ship yesterday?"
    assert not line.members_only and line.interaction_kind is None
    [skipped] = await deliveries_of_line(told["message_id"])
    assert (skipped.status.value, skipped.error) == ("SKIPPED", told["reason"])
    listed = await authenticated_client.get(
        f"/pods/{pod_id}/channels", params={"kind": "DIRECT"}
    )
    [direct] = listed.json()["items"]
    assert direct["id"] == told["channel_id"]
    assert direct["unread"]["lines"] >= 1


async def test_nobody_outside_the_pod_is_ever_a_recipient(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
):
    """404, not 422: an id naming nobody and an id naming somebody outside the
    pod are the same fact from the caller's side, and separating them would
    confirm the person exists. And below the API the service fails closed, so
    no wiring bug becomes "any user id can be messaged"."""
    pod_id = test_pod["id"]
    response = await authenticated_client.post(
        f"/pods/{pod_id}/notices", json={"to": str(uuid4()), "text": "Nope."}
    )
    assert response.status_code == 404, response.text

    outsider = uuid4()
    with pytest.raises(AgentSurfaceValidationError):
        await build_direct_messages(SqlAlchemyUnitOfWork(db_session)).send(
            DirectMessage(pod_id=UUID(pod_id), recipient_user_id=outsider, text="Hi")
        )
    await db_session.rollback()
    assert (
        await db_session.scalar(
            select(ChannelModel.id).where(ChannelModel.member_user_id == outsider)
        )
    ) is None


async def test_a_retried_send_with_the_same_key_is_the_same_line(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
):
    """A worker that retries an agent's ``channel_post`` replays the same call;
    the key makes it the first line, not a second line or a second email."""
    pod_id, owner = test_pod["id"], UUID(fixed_test_user["id"])
    await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
    )

    async def send():
        return await send_direct_message(
            pod_id=UUID(pod_id),
            recipient_user_id=owner,
            text="Does the 14th work for the offsite?",
            actor_user_id=owner,
            actor_agent_id=None,
            agent_name=None,
            origin_conversation_id=None,
            origin_run_id=None,
            origin_surface_id=None,
            wants_answer=True,
            key="run-1:call-1",
        )

    first = await send()
    again = await send()

    assert (first.repeated, again.repeated) == (False, True)
    assert again.message_id == first.message_id
    assert first.delivered_via == "RESEND"
    assert len(message_store.get_all("RESEND")) == 1
    lines = await direct_lines(db_session, pod_id=pod_id, user_id=owner)
    assert [line.id for line in lines] == [first.message_id]
    assert lines[0].interaction_kind == "ASK"
    assert lines[0].awaits_user_id == owner


async def test_a_line_cold_opens_an_email_thread_the_reply_can_find(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
):
    """The mail goes out, it is *not* prefixed "Re:", it carries a seed in
    ``References``, and a reply quoting that seed lands in the member's own
    channel -- the place the mail made -- not at a second one of its own."""
    from app.modules.agent_surfaces.domain.ingress_request import (
        SurfacePlatformWebhookIngress,
    )
    from app.modules.agent_surfaces.tests.e2e.helpers import (
        _conversation_by_external_thread,
        _resend_payload,
    )
    from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
        wait_for_messages,
    )
    from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
        process_ingress_and_run_scripted,
        script_text,
    )
    from app.modules.channels.infrastructure.models import ChannelSourceModel

    pod_id = test_pod["id"]
    address = await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
    )

    told = await _tell(
        authenticated_client, pod_id, fixed_test_user["email"], title="Standup"
    )

    assert told["delivered_via"] == "RESEND", told
    [delivery] = await deliveries_of_line(told["message_id"])
    assert (delivery.platform, delivery.status.value) == ("RESEND", "SENT")
    sent = json.loads(
        json.dumps((await wait_for_messages(message_store, "RESEND", min_count=1))[-1])
    )
    # First contact is not a reply, and must not read as one.
    assert sent.get("subject") == "Standup"
    seed = json.dumps(sent).split('"References": "')[1].split('"')[0]
    assert seed.startswith("<lemma-")

    # Their MUA answers with References = [our seed, their message id], so the
    # parser's thread root is our seed.
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload={
                **_resend_payload(
                    sender_email=fixed_test_user["email"],
                    assistant_address=address,
                    message_id="reply-to-standup",
                    text="Shipped the importer and reviewed two PRs.",
                ),
                "references": [seed, "<reply-to-standup@resend-e2e.test>"],
            },
            headers={},
        ),
        script=[script_text("Thanks, recorded.")],
    )

    threaded = await _conversation_by_external_thread(
        authenticated_client, pod_id=pod_id, external_thread_id=seed
    )
    assert threaded is not None, "the reply did not land in the mail's thread"
    [place] = (
        await db_session.execute(
            select(ChannelSourceModel).where(
                ChannelSourceModel.external_channel_id == seed
            )
        )
    ).scalars()
    assert place.kind == "PRIVATE"
    assert str(place.channel_id) == told["channel_id"]
    channel = await db_session.get(ChannelModel, place.channel_id)
    assert channel is not None and channel.kind == "DIRECT"
    assert channel.member_user_id == UUID(fixed_test_user["id"])


async def test_a_line_for_somebody_else_says_on_whose_behalf(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    message_store,
):
    """The pod's agent writes for one member to another: the mail says whose
    words they are, and the line is in the recipient's channel, not the writer's."""
    pod_id = test_pod["id"]
    await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
    )
    colleague = await _colleague(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
    )

    told = await _tell(
        authenticated_client, pod_id, colleague["email"], text="Review the Q3 deck?"
    )

    assert told["delivered_via"] == "RESEND", told
    [mail] = message_store.get_all("RESEND")
    assert mail["to"] == [colleague["email"]]
    assert mail["text"].startswith("On behalf of ")
    assert "Review the Q3 deck?" in mail["text"]
    # One line: the mail carried it, and is not a second line beside it.
    [line] = await direct_lines(db_session, pod_id=pod_id, user_id=colleague["id"])
    assert line.on_behalf_user_id == UUID(fixed_test_user["id"])
    assert (
        await direct_lines(db_session, pod_id=pod_id, user_id=fixed_test_user["id"])
        == []
    )


async def test_a_named_platform_is_honoured_or_refused_never_swapped(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
):
    """An agent names a platform for reasons the router cannot see. One that
    cannot carry it is refused, with what would work -- never quietly swapped
    for email, which would leave the agent certain of something untrue."""
    pod_id, owner = test_pod["id"], UUID(fixed_test_user["id"])
    await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
    )

    async def send(platform: str):
        return await send_direct_message(
            pod_id=UUID(pod_id),
            recipient_user_id=owner,
            text=f"By {platform}, please.",
            actor_user_id=owner,
            actor_agent_id=None,
            agent_name=None,
            origin_conversation_id=None,
            origin_run_id=None,
            origin_surface_id=None,
            wants_answer=False,
            key=None,
            platform=platform,
        )

    refused = await send("telegram")
    assert refused.delivered_via is None
    assert refused.reason and "telegram" in refused.reason.lower()
    assert message_store.get_all("RESEND") == []
    [skipped] = await deliveries_of_line(refused.message_id)
    assert skipped.status.value == "SKIPPED"

    honoured = await send("email")
    assert honoured.delivered_via == "RESEND"
    assert len(message_store.get_all("RESEND")) == 1


async def test_a_channel_read_in_lemma_only_is_not_pushed(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
):
    """Their channel's notifications are theirs to turn off; the line still
    lands, and nothing is mailed."""
    pod_id = test_pod["id"]
    await _resend_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
    )
    listed = await authenticated_client.get(
        f"/pods/{pod_id}/channels", params={"kind": "DIRECT"}
    )
    [direct] = listed.json()["items"]
    quiet = await authenticated_client.put(
        f"/pods/{pod_id}/channels/{direct['id']}/me", json={"notify": "NONE"}
    )
    assert quiet.status_code == 200, quiet.text

    told = await _tell(authenticated_client, pod_id, fixed_test_user["email"])

    assert told["delivered_via"] is None
    assert told["reason"] == "They read this channel in Lemma only."
    assert message_store.get_all("RESEND") == []
    assert told["channel_id"] == direct["id"]


async def test_the_agent_is_told_only_about_lines_that_ask_something(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """The prompt must not name a line nobody can answer.

    Everything open used to be rendered as "Open requests for this person";
    an informational notice was rendered exactly like a question, the agent
    answered it, and the answer was refused. Only a line waiting on them --
    asked by some other conversation -- is a question to the agent here.
    """
    pod_id, owner = test_pod["id"], UUID(fixed_test_user["id"])

    async def send(text: str, *, wants_answer: bool):
        return await send_direct_message(
            pod_id=UUID(pod_id),
            recipient_user_id=owner,
            text=text,
            actor_user_id=owner,
            actor_agent_id=None,
            agent_name=None,
            origin_conversation_id=None,
            origin_run_id=None,
            origin_surface_id=None,
            wants_answer=wants_answer,
            key=None,
        )

    asking = await send("Standup: what did you ship?", wants_answer=True)
    await send("Deploy finished.", wants_answer=False)

    rendered = await open_question_text(
        conversation_id=uuid4(),
        channel_id=asking.channel_id,
        pod_id=UUID(pod_id),
        user_id=owner,
    )

    assert rendered is not None
    assert str(asking.message_id) in rendered
    assert "Standup: what did you ship?" in rendered
    assert "Deploy finished." not in rendered


async def test_a_pod_with_nothing_connected_mints_itself_a_readable_mailbox(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The dev failure, against a real database and the real surface service.

    A personal pod with no surfaces at all, asked to message a member, reported
    "the pod has no active surface to reach anyone on" and created nothing. The
    unit tests around this stub the provisioner, so they can prove routing
    *asks* for a mailbox but not that a usable one comes back.

    This asserts the surface that actually lands: owned by the pod's own
    assistant rather than any named agent, and carrying an address a person
    could be asked to type.
    """
    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")

    pod_id = test_pod["id"]
    assert await _pod_surfaces(db_session, pod_id) == [], (
        "this test is only meaningful in a pod with no surfaces"
    )

    told = await _tell(authenticated_client, pod_id, fixed_test_user["id"])

    # Not stopped for want of a surface: one was created to carry it.
    assert told["reason"] != NO_SURFACE
    [surface] = await _pod_surfaces(db_session, pod_id)
    assert surface.surface_type == "RESEND"
    # The assistant's, whose row id is its pod's. It used to be *nobody's*,
    # which is what made one column mean two things.
    assert surface.agent_id == UUID(pod_id)
    assert surface.surface_identity_email.endswith("@ops.example.com")
    # Readable, not pod-<32 hex chars>: people are asked to write to this.
    assert not surface.surface_identity_email.split("@")[0].startswith("pod-")


async def test_a_second_pod_in_the_org_also_gets_a_mailbox(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The dev failure exactly: the org already has a mailbox, this pod wants one.

    ``ensure_unique_org_credential_binding`` treated the system Resend key as a
    claimable identity, like a Slack app or a WhatsApp number. It is not -- it is
    one API key over a catch-all domain, and every surface gets its own unique
    address off it. So the first mailbox created anywhere in an organization
    refused every mailbox after it.

    Two pods, one organization, one line each.
    """
    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")

    second = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Second Pod {uuid4()}",
            "slug": f"second-pod-{uuid4()}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
        follow_redirects=True,
    )
    assert second.status_code in (200, 201), second.text
    second_pod_id = second.json()["id"]

    # The first pod claims a mailbox, as it would on any deployment.
    await _tell(authenticated_client, test_pod["id"], fixed_test_user["id"])
    # The second pod asks for one while that claim exists.
    told = await _tell(authenticated_client, second_pod_id, fixed_test_user["id"])

    assert "creating a mailbox" not in (told["reason"] or "")
    addresses = {
        row.pod_id: row.surface_identity_email
        for row in await _pod_surfaces(db_session, test_pod["id"], second_pod_id)
    }
    assert len(addresses) == 2, "both pods should hold a mailbox of their own"
    # Distinct addresses are what makes sharing the key safe: inbound routes on
    # the address, which carries a unique index.
    assert len(set(addresses.values())) == 2


async def test_resend_mailbox_is_blocked_on_a_local_url_without_polling(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The desktop-app failure, reproduced against the real surface service.

    On a localhost API URL with no public webhook and polling mode off, the
    Resend mailbox cannot be provisioned (the runtime gate demands a public
    HTTPS callback), so the line stays in Lemma and no surface lands.
    """
    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")
    # Override the e2e's public HTTPS URL back to the desktop app's reality.
    monkeypatch.setattr(core_settings, "api_url", "http://localhost:8711")
    monkeypatch.setattr(surface_settings, "enable_resend_polling_mode", False)

    pod_id = test_pod["id"]
    told = await _tell(authenticated_client, pod_id, fixed_test_user["id"])

    assert told["delivered_via"] is None
    assert "creating a mailbox for it failed" in (told["reason"] or "")
    assert await _pod_surfaces(db_session, pod_id) == [], (
        "no surface should be provisioned when the gate blocks it"
    )


async def test_resend_mailbox_is_minted_on_a_local_url_with_polling(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The fix: ENABLE_RESEND_POLLING_MODE lets the desktop app (localhost, no
    public webhook) mint a Resend mailbox, so a member can be mailed.
    """
    from app.core.config import settings as core_settings
    from app.modules.agent_surfaces.config import surface_settings

    monkeypatch.setattr(core_settings, "resend_api_key", "re_test")
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.example.com")
    monkeypatch.setattr(core_settings, "api_url", "http://localhost:8711")
    monkeypatch.setattr(surface_settings, "enable_resend_polling_mode", True)

    pod_id = test_pod["id"]
    told = await _tell(authenticated_client, pod_id, fixed_test_user["id"])

    # The mailbox was minted, so this is not the "no surface / provision failed"
    # branch. Same localhost URL as the test above -- only polling mode differs.
    assert "creating a mailbox for it failed" not in (told["reason"] or "")
    [surface] = await _pod_surfaces(db_session, pod_id)
    assert surface.surface_type == "RESEND"
    assert surface.surface_identity_email.endswith("@ops.example.com")
