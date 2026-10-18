"""The messaging toolset: finding a colleague, and the pod's channels.

``channel_post`` with ``to`` resolves a pod member id, a user id, or an exact
email address and nothing else -- a bare "Priya" returns None. That made it
unusable unless an id happened to be sitting in the conversation, which is the
gap ``list_pod_members`` closes: what comes out of the lookup must go straight
into ``channel_post`` unchanged.

The rest pins what each tool asks of the modules it composes. Doubles stand in
for those modules' published operations -- ``agent_surfaces``' direct-message
contract, and the ``channels`` reads the adapter imports by name -- never for
the tool itself: a green test here proves the tool's half, and the e2e suites
prove the other.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging import pydantic_adapter as adapter
from app.modules.agent.tools.messaging.models import (
    ChannelListRequest,
    ChannelPostRequest,
    ChannelReadRequest,
    ChannelSearchRequest,
    ListPodMembersRequest,
    MessageChannel,
)
from app.modules.agent.tools.messaging.pydantic_adapter import (
    channel_list,
    channel_post,
    channel_read,
    channel_search,
    line_sender_name,
    list_pod_members,
)
from app.modules.agent_surfaces.contracts.direct_messages import Sent
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Interaction,
    InteractionKind,
    InteractionState,
    Principal,
    Sender,
    SenderKind,
    Source,
    SourceKind,
)
from app.modules.pod.contracts.directory import PodDirectoryMember, PodDirectoryPage

pytestmark = pytest.mark.unit

PRIYA = uuid4()
BOB = uuid4()
ME = uuid4()
# The pod-member id and the user id are different ids for the same person, and
# reachability is keyed by the second: holding one value for both would let a
# test pass while the tool looked reach up under the wrong key.
PRIYA_USER = uuid4()
BOB_USER = uuid4()
NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def _ctx(
    pod_id=None, user_id=None, *, run_id=None, tool_call_id=None
) -> RunContext[BaseAgentContext]:
    return RunContext(
        deps=BaseAgentContext(
            user_id=user_id or ME,
            pod_id=pod_id or uuid4(),
            conversation_id=uuid4(),
            agent_run_id=run_id,
        ),
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
        tool_call_id=tool_call_id,
    )


def _podless_ctx() -> RunContext[BaseAgentContext]:
    """``pod_id`` is a required UUID, so this state cannot be built normally.

    ``model_construct`` skips validation to reach the defensive branch anyway --
    the same guard every tool here carries, kept for consistency rather than
    because the model can get here.
    """
    deps = BaseAgentContext.model_construct(
        user_id=ME, pod_id=None, conversation_id=uuid4()
    )
    return RunContext(
        deps=deps,
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
    )


# ------------------------------------------------------------ list_pod_members


def _members() -> list[PodDirectoryMember]:
    return [
        PodDirectoryMember(
            to=str(PRIYA),
            name="Priya Sharma",
            email="priya@example.com",
            role="USER",
            is_you=False,
            user_id=PRIYA_USER,
        ),
        PodDirectoryMember(
            to=str(BOB),
            name="Bob Jones",
            email="bob@example.com",
            role="ADMIN",
            is_you=True,
            user_id=BOB_USER,
        ),
    ]


def _page(members, total, truncated) -> PodDirectoryPage:
    return PodDirectoryPage(
        members=tuple(members), total_matched=total, truncated=truncated
    )


def _patch(monkeypatch, result, reach=None):
    """Double the two published operations the tool composes, where they live.

    Not on the tool module: this tool's whole job is the composition -- ask
    `pod` who is in the pod, ask `agent_surfaces` where they can be reached,
    and hand the model one row per person.
    """

    async def _fake(*, pod_id, requester_user_id, search, limit):
        _fake.seen = {
            "search": search,
            "limit": limit,
            "pod_id": pod_id,
        }
        return result

    async def _reach(*, pod_id, recipients, actor_agent_id):
        _fake.seen["actor_agent_id"] = actor_agent_id
        _fake.seen["recipients"] = recipients
        return dict(reach or {})

    monkeypatch.setattr("app.modules.pod.contracts.directory.list_pod_members", _fake)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.contracts.direct_messages.reachable_channels",
        _reach,
    )
    return _fake


async def test_the_lookup_hands_back_something_channel_post_accepts(monkeypatch):
    """The whole point: `to` is copied through, not re-derived by the model."""
    _patch(monkeypatch, _page(_members(), 2, False))

    result = await list_pod_members(_ctx(uuid4()), ListPodMembersRequest())

    assert result.success is True
    assert [m.to for m in result.members] == [str(PRIYA), str(BOB)]
    # Every `to` is a real pod member id -- the form resolve_recipient tries
    # first -- rather than a display name the resolver would reject.
    for member in result.members:
        UUID(member.to)
    assert "channel_post" in (result.message or "")


async def test_a_bare_first_name_is_passed_through_as_the_search(monkeypatch):
    """The literal scenario that made messaging by name unusable."""
    fake = _patch(monkeypatch, _page([_members()[0]], 1, False))

    result = await list_pod_members(
        _ctx(uuid4()), ListPodMembersRequest(search="priya")
    )

    assert fake.seen["search"] == "priya"
    assert result.members[0].name == "Priya Sharma"


async def test_the_caller_is_flagged_so_the_agent_knows_who_it_is(monkeypatch):
    """Reaching the run's own owner needs no permission; the others do."""
    _patch(monkeypatch, _page(_members(), 2, False))

    result = await list_pod_members(_ctx(uuid4()), ListPodMembersRequest())

    assert [m.is_you for m in result.members] == [False, True]


async def test_no_pod_access_is_an_error_result_not_an_exception(monkeypatch):
    """A tool must hand the model something it can act on, not a traceback."""
    _patch(monkeypatch, None)

    result = await list_pod_members(_ctx(uuid4()), ListPodMembersRequest())

    assert result.success is False
    assert "access" in (result.error or "").lower()


async def test_an_empty_match_says_so_rather_than_looking_broken(monkeypatch):
    _patch(monkeypatch, _page([], 0, False))

    result = await list_pod_members(
        _ctx(uuid4()), ListPodMembersRequest(search="nobody")
    )

    assert result.success is True
    assert result.members == []
    assert "nobody" in (result.message or "")


async def test_outside_a_pod_the_tool_refuses(monkeypatch):
    result = await list_pod_members(_podless_ctx(), ListPodMembersRequest())

    assert result.success is False
    assert "pod" in (result.error or "").lower()


async def test_truncation_tells_the_model_to_narrow_the_search(monkeypatch):
    """Silently returning a partial list is how an agent messages the wrong person."""
    _patch(monkeypatch, _page(_members(), 87, True))

    result = await list_pod_members(_ctx(uuid4()), ListPodMembersRequest())

    assert result.truncated is True
    assert result.total_matched == 87
    assert "87" in (result.message or "")


async def test_the_lookup_says_which_channels_can_reach_each_person(monkeypatch):
    """So that naming a platform is reading an answer, not guessing at one.

    Reach is `agent_surfaces`' answer, joined onto `pod`'s member list by user
    id. The two are separate lookups, so this also pins the join: a member the
    reach lookup said nothing about must read as "their channel in Lemma only",
    never as somebody else's platforms.
    """
    _patch(
        monkeypatch,
        _page(_members(), 2, False),
        reach={PRIYA_USER: ["email", "telegram"]},
    )

    result = await list_pod_members(_ctx(), ListPodMembersRequest())

    assert result.members[0].reachable_on == ["email", "telegram"]
    assert result.members[1].reachable_on == []


async def test_reach_is_asked_only_about_the_people_being_returned(monkeypatch):
    """Reachability costs queries per surface; rows nobody sees are not worth it."""
    fake = _patch(monkeypatch, _page(_members(), 2, False))

    await list_pod_members(_ctx(), ListPodMembersRequest())

    assert set(fake.seen["recipients"]) == {PRIYA_USER, BOB_USER}


# ------------------------------------------------------- channel_post, to a member


def _sent(monkeypatch, *, recipient=PRIYA_USER, **result):
    """Stand in for the direct-message contract, and record what it was asked."""
    sent = Sent(
        message_id=uuid4(),
        channel_id=uuid4(),
        delivered_via=result.get("delivered_via", "telegram"),
        reason=result.get("reason"),
        repeated=False,
    )

    async def _resolve(*, pod_id, reference):
        _send.resolved = reference
        return recipient

    async def _send(**kwargs):
        _send.seen = kwargs
        return sent

    _send.seen = None
    monkeypatch.setattr(
        "app.modules.agent_surfaces.contracts.direct_messages.resolve_recipient",
        _resolve,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.contracts.direct_messages.send_direct_message",
        _send,
    )
    _send.sent = sent
    return _send


async def test_a_message_lands_in_the_members_channel_and_says_where(monkeypatch):
    """The agent is told the line's id and its channel, and how it travelled --
    a message always lands in their channel in Lemma, whether or not a
    platform carried it."""
    send = _sent(monkeypatch, delivered_via="telegram")
    ctx = _ctx()

    result = await channel_post(ctx, ChannelPostRequest(text="Standup?", to=str(PRIYA)))

    assert result.success is True
    assert result.message_id == send.sent.message_id
    assert result.channel_id == send.sent.channel_id
    assert result.delivered_via == "telegram"
    assert "telegram" in (result.message or "")
    assert send.seen["recipient_user_id"] == PRIYA_USER
    assert send.seen["text"] == "Standup?"
    assert send.seen["actor_user_id"] == ctx.deps.user_id
    # The conversation an answer wakes is the one that asked.
    assert send.seen["origin_conversation_id"] == ctx.deps.conversation_id
    assert send.seen["wants_answer"] is False


async def test_a_name_that_resolves_to_nobody_points_at_the_lookup(monkeypatch):
    """Refused before anything is sent, with the tool that fixes it named."""
    send = _sent(monkeypatch, recipient=None)

    result = await channel_post(_ctx(), ChannelPostRequest(text="hi", to="Priya"))

    assert result.success is False
    assert "list_pod_members" in (result.error or "")
    assert send.seen is None


@pytest.mark.parametrize(
    "request_",
    [
        ChannelPostRequest(text="hi"),
        ChannelPostRequest(text="hi", to="priya@example.com", channel_id=uuid4()),
    ],
    ids=["neither", "both"],
)
async def test_a_post_names_exactly_one_place(monkeypatch, request_):
    """A member or a channel: guessing between two would post somewhere the
    agent did not mean."""
    send = _sent(monkeypatch)

    result = await channel_post(_ctx(), request_)

    assert result.success is False
    assert send.seen is None


async def test_asking_says_to_end_the_turn_rather_than_wait(monkeypatch):
    """An answer never comes back as a tool result: the asking conversation
    gets a fresh turn once everyone it asked has answered. Without saying so,
    the model polls."""
    send = _sent(monkeypatch)

    result = await channel_post(
        _ctx(), ChannelPostRequest(text="Ship it?", to=str(PRIYA), wants_answer=True)
    )

    assert send.seen["wants_answer"] is True
    assert "end your turn" in (result.message or "")


async def test_a_retried_call_is_the_same_message_by_its_key(monkeypatch):
    """A worker job retried replays this exact call. The key -- the run and
    the tool call -- is what makes it the same line rather than a second one
    in the member's channel."""
    send = _sent(monkeypatch)
    run_id = uuid4()

    await channel_post(
        _ctx(run_id=run_id, tool_call_id="call-7"),
        ChannelPostRequest(text="hi", to=str(PRIYA)),
    )
    assert send.seen["key"] == f"{run_id}:call-7"

    await channel_post(_ctx(), ChannelPostRequest(text="hi", to=str(PRIYA)))
    assert send.seen["key"] is None


async def test_the_pod_assistant_does_not_claim_to_be_an_agent_row(monkeypatch):
    """A delegation token can still name the assistant by the sentinel
    `00000000-…-0001`, which is not a row in `agents` -- tokens are signed and
    outlive the deploy that stopped issuing them. The assistant has a row now,
    whose id is the pod's own: that is the id the message is attributed to,
    under the name it answers to (this value becomes a chat bot's username and
    an email `From`, and `pod_default` is the row's, not a name).
    """
    from app.core.authorization.delegation import (
        DEFAULT_POD_AGENT_ID,
        DEFAULT_RESPONDER_NAME,
    )

    send = _sent(monkeypatch)
    ctx = _ctx(uuid4())
    ctx.deps.workload_id = DEFAULT_POD_AGENT_ID
    ctx.deps.is_pod_default_agent = True
    ctx.deps.agent_name = "pod_default"
    ctx.deps.agent_display_name = None

    result = await channel_post(
        ctx, ChannelPostRequest(text="hi", to="someone@example.com")
    )

    assert result.success is True
    assert send.seen["actor_agent_id"] == ctx.deps.pod_id
    assert send.seen["agent_name"] == DEFAULT_RESPONDER_NAME


async def test_a_real_agent_still_reports_its_own_id(monkeypatch):
    agent_id = uuid4()
    send = _sent(monkeypatch)
    ctx = _ctx(uuid4())
    ctx.deps.workload_id = agent_id
    ctx.deps.is_pod_default_agent = False
    ctx.deps.agent_display_name = "Ops"

    await channel_post(ctx, ChannelPostRequest(text="hi", to="someone@example.com"))

    assert send.seen["actor_agent_id"] == agent_id
    assert send.seen["agent_name"] == "Ops"


async def test_the_platform_the_agent_named_reaches_the_router(monkeypatch):
    """Passed as the plain platform name, which is the whole vocabulary.

    The tool speaks in platforms because that is what an agent can reason
    about; surface ids and mail providers are not things it can choose between.
    """
    send = _sent(monkeypatch, delivered_via="whatsapp")

    await channel_post(
        _ctx(),
        ChannelPostRequest(
            text="Standup?", to=str(PRIYA), platform=MessageChannel.WHATSAPP
        ),
    )

    assert send.seen["platform"] == "whatsapp"


async def test_leaving_the_platform_out_leaves_the_routing_alone(monkeypatch):
    """The default has to stay the default: no platform means no filter."""
    send = _sent(monkeypatch)

    await channel_post(_ctx(), ChannelPostRequest(text="Standup?", to=str(PRIYA)))

    assert send.seen["platform"] is None


async def test_a_message_nothing_carried_still_landed_and_says_why(monkeypatch):
    """Not a failure: the line is in their channel in Lemma. The reason is what
    lets the model see that dropping its own `platform` would reach them,
    rather than concluding the person is unreachable."""
    _sent(
        monkeypatch,
        delivered_via=None,
        reason=(
            "They have not messaged this agent on WhatsApp. Nothing was sent "
            "elsewhere; email would reach them."
        ),
    )

    result = await channel_post(
        _ctx(),
        ChannelPostRequest(
            text="Standup?", to=str(PRIYA), platform=MessageChannel.WHATSAPP
        ),
    )

    assert result.success is True
    assert result.delivered_via is None
    assert "their channel in Lemma" in (result.message or "")
    assert "email would reach them" in (result.message or "")


async def test_outside_a_pod_nothing_is_posted(monkeypatch):
    send = _sent(monkeypatch)

    result = await channel_post(
        _podless_ctx(), ChannelPostRequest(text="hi", to=str(PRIYA))
    )

    assert result.success is False
    assert send.seen is None


# ------------------------------------------------------ the channels, as a member


class _FakeUoW:
    def __init__(self):
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def commit(self) -> None:
        self.committed = True


def _channel(kind: ChannelKind, *, pod_id: UUID, member: UUID | None = None):
    return Channel(
        id=uuid4(),
        pod_id=pod_id,
        kind=kind,
        title=f"{kind.value.lower()} channel",
        member_user_id=member if kind is ChannelKind.DIRECT else None,
        contact_id=uuid4() if kind is ChannelKind.CONTACT else None,
        created_at=NOW,
    )


def _line(channel_id: UUID, text: str, *, sender=None, **extra) -> ChannelMessage:
    return ChannelMessage(
        id=uuid4(),
        channel_id=channel_id,
        sender=sender or Sender.member(uuid4(), display_name="Priya"),
        text=text,
        created_at=NOW,
        **extra,
    )


def _channels(monkeypatch, *, readable: Channel | None = None, sees_contacts=True):
    """Double the channel reads the adapter imports, and the permission check.

    Each records how it was asked, so a test can pin that the member's own id
    and their right to see contacts reached the read -- which is what decides
    what they may see.
    """
    seen: dict = {"uow": _FakeUoW(), "posted": []}

    async def _post(uow, *, channel_id, sender, text, **written):
        seen["posted"].append(
            {"channel_id": channel_id, "sender": sender, "text": text, **written}
        )
        return _line(channel_id, text, sender=sender)

    async def _holds(uow, pod_id, user_id, permission):
        seen["holds"] = (pod_id, user_id, permission)
        return sees_contacts

    async def _may_read(uow, *, pod_id, channel_id, user_id, sees_contacts):
        seen["may_read"] = {
            "pod_id": pod_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "sees_contacts": sees_contacts,
        }
        return readable

    monkeypatch.setattr(adapter, "_uow", lambda: seen["uow"])
    monkeypatch.setattr(adapter, "post", _post)
    monkeypatch.setattr("app.modules.pod.contracts.members.pod_member_holds", _holds)
    monkeypatch.setattr(
        "app.modules.channels.contracts.lines.member_may_read", _may_read
    )
    return seen


async def test_a_post_in_a_channel_is_the_agents_on_the_members_behalf(monkeypatch):
    """Attribution: the line says which agent wrote it and whose access it
    used, so a member reading the timeline is not shown an answer made from
    somebody else's data as though it were theirs."""
    ctx = _ctx(run_id=uuid4())
    group = _channel(ChannelKind.GROUP, pod_id=ctx.deps.pod_id)
    seen = _channels(monkeypatch, readable=group)

    result = await channel_post(
        ctx, ChannelPostRequest(text="Shipped.", channel_id=group.id)
    )

    assert result.success is True
    assert result.channel_id == group.id
    [posted] = seen["posted"]
    assert posted["channel_id"] == group.id
    assert posted["sender"].kind is SenderKind.AGENT
    assert posted["sender"].on_behalf_of == Principal.member(ctx.deps.user_id)
    assert posted["conversation_id"] == ctx.deps.conversation_id
    assert posted["agent_run_id"] == ctx.deps.agent_run_id
    assert seen["uow"].committed is True
    assert seen["may_read"]["user_id"] == ctx.deps.user_id


async def test_a_channel_the_member_cannot_read_is_not_written_in(monkeypatch):
    """Another member's own channel, or a contact's for somebody who may not
    see contacts: the same answer as a channel that does not exist."""
    seen = _channels(monkeypatch, readable=None)

    result = await channel_post(
        _ctx(), ChannelPostRequest(text="hi", channel_id=uuid4())
    )

    assert result.success is False
    assert "No such channel" in (result.error or "")
    assert seen["posted"] == []


async def test_an_agent_does_not_write_into_a_contacts_channel(monkeypatch):
    """A contact's channel is answered by the pod's bot or a member who steps
    in -- never by a member's agent, whose words would reach the contact
    written with the member's access."""
    ctx = _ctx()
    seen = _channels(
        monkeypatch, readable=_channel(ChannelKind.CONTACT, pod_id=ctx.deps.pod_id)
    )

    result = await channel_post(ctx, ChannelPostRequest(text="hi", channel_id=uuid4()))

    assert result.success is False
    assert seen["posted"] == []


async def test_who_may_see_contacts_decides_which_channels_are_read(monkeypatch):
    """The permission is read as the run's member, and passed to the read."""
    ctx = _ctx()
    seen = _channels(monkeypatch, readable=None, sees_contacts=False)

    await channel_read(ctx, ChannelReadRequest(channel_id=uuid4()))

    assert seen["holds"][1] == ctx.deps.user_id
    assert seen["may_read"]["sees_contacts"] is False


async def test_reading_a_channel_shows_its_lines_and_hides_what_is_not_theirs(
    monkeypatch,
):
    """An agent's answer made with another member's access is shown as given,
    not what it said; a deleted line is not shown at all."""
    ctx = _ctx()
    group = _channel(ChannelKind.GROUP, pod_id=ctx.deps.pod_id)
    _channels(monkeypatch, readable=group)
    someone_else = uuid4()
    lines = [
        _line(group.id, "Who has the deck?"),
        _line(
            group.id,
            "Margin is 31%.",
            sender=Sender.agent(
                uuid4(),
                on_behalf_of=Principal.member(someone_else),
                display_name="Ops",
            ),
        ),
        _line(group.id, "gone", deleted_at=NOW),
    ]

    async def _recent(uow, *, channel_id, limit):
        assert channel_id == group.id
        return lines

    monkeypatch.setattr("app.modules.channels.contracts.lines.recent_lines", _recent)

    result = await channel_read(ctx, ChannelReadRequest(channel_id=group.id))

    assert result.success is True
    assert [line.sender for line in result.lines] == ["Priya", "Ops"]
    assert result.lines[0].text == "Who has the deck?"
    assert result.lines[1].text is None


async def test_a_read_names_a_channel_or_asks_for_the_questions(monkeypatch):
    _channels(monkeypatch)

    result = await channel_read(_ctx(), ChannelReadRequest())

    assert result.success is False


def _question(text: str, *, state: InteractionState, answer=None) -> ChannelMessage:
    return _line(
        uuid4(),
        text,
        sender=Sender.agent(uuid4(), on_behalf_of=Principal.member(ME)),
        interaction=Interaction(
            kind=InteractionKind.ASK,
            state=state,
            question={},
            awaits=Principal.member(PRIYA_USER),
            answer=answer,
        ),
    )


async def test_asked_by_me_reads_this_conversations_questions_and_answers(
    monkeypatch,
):
    """What a turn opened by "they have answered" reads: every question this
    conversation put to people -- by its ref prefix, never another
    conversation's -- with who it waits on and what came back."""
    ctx = _ctx()
    seen = _channels(monkeypatch)
    asked = [
        _question(
            "Ship it?",
            state=InteractionState.ANSWERED,
            answer={"text": "Yes, Friday."},
        ),
        _question("Budget?", state=InteractionState.OPEN),
    ]

    async def _under(uow, *, prefix):
        seen["prefix"] = prefix
        return asked

    monkeypatch.setattr(
        "app.modules.channels.contracts.questions.questions_under", _under
    )

    result = await channel_read(ctx, ChannelReadRequest(asked_by_me=True))

    assert seen["prefix"] == f"msg:{ctx.deps.conversation_id}:"
    assert result.success is True
    assert [line.question_state for line in result.lines] == ["ANSWERED", "OPEN"]
    assert result.lines[0].answer == {"text": "Yes, Friday."}
    assert result.lines[0].awaits == str(PRIYA_USER)
    assert result.message == "1 answered, 1 still waiting."


async def test_search_reads_only_the_members_channels_and_drops_the_withheld(
    monkeypatch,
):
    """The words are looked for in the channels the member can see, and a line
    they would be shown only as "answered" is not a search hit: its text is
    the thing being hidden."""
    ctx = _ctx()
    seen = _channels(monkeypatch)
    group = _channel(ChannelKind.GROUP, pod_id=ctx.deps.pod_id)
    mine = _channel(ChannelKind.DIRECT, pod_id=ctx.deps.pod_id, member=ME)

    async def _of_member(uow, *, pod_id, user_id, sees_contacts, kind=None):
        seen["of_member"] = (user_id, sees_contacts, kind)
        return [(group, []), (mine, [])]

    agent_for_other = Sender.agent(
        uuid4(), on_behalf_of=Principal.member(uuid4()), display_name="Ops"
    )
    agent_for_me = Sender.agent(
        uuid4(), on_behalf_of=Principal.member(uuid4()), display_name="Ops"
    )
    found = [
        _line(group.id, "salaries are in the sheet"),
        _line(group.id, "salaries: 1.2M", sender=agent_for_other),
        # In the member's own channel: never withheld from them.
        _line(mine.id, "salaries, as you asked", sender=agent_for_me),
    ]

    async def _search(uow, *, channel_ids, words, limit):
        seen["search"] = (set(channel_ids), words)
        return found

    monkeypatch.setattr(
        "app.modules.channels.contracts.lines.channels_of_member", _of_member
    )
    monkeypatch.setattr("app.modules.channels.contracts.lines.search_lines", _search)

    result = await channel_search(ctx, ChannelSearchRequest(query="salaries"))

    assert seen["of_member"][0] == ctx.deps.user_id
    assert seen["search"] == ({group.id, mine.id}, "salaries")
    assert [line.text for line in result.lines] == [
        "salaries are in the sheet",
        "salaries, as you asked",
    ]


async def test_the_channel_list_says_where_each_is_fed_from(monkeypatch):
    ctx = _ctx()
    seen = _channels(monkeypatch)
    group = _channel(ChannelKind.GROUP, pod_id=ctx.deps.pod_id)
    source = Source(
        id=uuid4(),
        channel_id=group.id,
        pod_id=ctx.deps.pod_id,
        surface_id=uuid4(),
        platform="telegram",
        kind=SourceKind.SHARED,
        title="Launch crew",
    )

    async def _of_member(uow, *, pod_id, user_id, sees_contacts, kind=None):
        seen["kind"] = kind
        return [(group, [source])]

    monkeypatch.setattr(
        "app.modules.channels.contracts.lines.channels_of_member", _of_member
    )

    result = await channel_list(ctx, ChannelListRequest(kind="group"))

    assert seen["kind"] is ChannelKind.GROUP
    assert result.channels[0].id == group.id
    assert result.channels[0].kind == "GROUP"
    assert result.channels[0].places == ["telegram: Launch crew"]


async def test_an_unknown_kind_is_refused_with_the_ones_that_exist(monkeypatch):
    _channels(monkeypatch)

    result = await channel_list(_ctx(), ChannelListRequest(kind="team"))

    assert result.success is False
    assert "DIRECT" in (result.error or "")


def test_a_sender_with_no_name_is_named_by_what_they_are():
    """The agent reads who said a line; a nameless one still says whether it
    was a member, a contact, or an agent -- never an id."""
    assert line_sender_name(_line(uuid4(), "x", sender=Sender.member(uuid4()))) == (
        "a member"
    )
    assert (
        line_sender_name(_line(uuid4(), "x", sender=Sender.contact(uuid4())))
        == "a contact"
    )
