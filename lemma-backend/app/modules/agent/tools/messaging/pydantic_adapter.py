"""Messaging toolset: the pod's channels, and reaching a person outside this
conversation.

The distinction that matters, and the one the docstrings spend their words on:

  ``ask_user``      pauses this turn and resumes it with the answer -- but only
                    for the person already here.
  ``channel_post``  reaches somebody else, or a channel, and returns at once.
                    Nothing pauses. With ``wants_answer`` it is a question
                    waiting on them in their inbox; once everyone this
                    conversation asked has answered, it gets a fresh turn, and
                    ``channel_read(asked_by_me=True)`` shows what they said.

Ending the turn is the whole trick, and it is why posting is safe to make
non-blocking: a person takes hours, and a finished turn holds nothing.

A run answering somebody outside the pod has one tool here, ``ask_a_person``:
it flags their channel as needing a member, who steps in and answers them.
"""

from __future__ import annotations

from uuid import UUID

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.authorization.delegation import agent_display_name, effective_agent_id
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.outsiders import run_audience
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.models import (
    MAX_OUTSIDER_MESSAGE_CHARS,
    AskAPersonRequest,
    AskAPersonResponse,
    ChannelListRequest,
    ChannelListResponse,
    ChannelPostRequest,
    ChannelPostResponse,
    ChannelReadRequest,
    ChannelReadResponse,
    ChannelSearchRequest,
    ChannelSearchResponse,
    ChannelSummary,
    LineSummary,
    ListPodMembersRequest,
    ListPodMembersResponse,
    PodMemberSummary,
)
from app.modules.agent.tools.messaging.teammates import ask_teammate, list_teammates
from app.modules.agent_surfaces.contracts import direct_messages
from app.modules.channels.contracts import (
    ChannelKind,
    ChannelMessage,
    Principal,
    Sender,
    post,
)
from app.modules.channels.contracts import lines as channel_reads
from app.modules.channels.contracts import SenderKind, withheld_from
from app.modules.channels.contracts import questions as channel_questions
from app.modules.pod.contracts import directory as pod_directory
from app.modules.pod.contracts import members as pod_members

logger = get_logger(__name__)

CHANNEL_POST_TOOL_NAME = "channel_post"


def _uow():
    return SessionUnitOfWorkFactory(async_session_maker)()


def _refused_to_outsiders(deps: BaseAgentContext) -> str | None:
    if run_audience(deps).answers_outsiders:
        return (
            "You are answering somebody outside the pod. When they need a "
            "person, use `ask_a_person`."
        )
    return None


def line_sender_name(line: ChannelMessage) -> str:
    """Who said a line, as the agent reads it."""
    sender = line.sender
    if sender.display_name:
        return sender.display_name
    return {
        SenderKind.MEMBER: "a member",
        SenderKind.CONTACT: "a contact",
        SenderKind.AGENT: "an agent",
    }[sender.kind]


def _summary(line: ChannelMessage, *, reader_id: UUID, in_direct: bool) -> LineSummary:
    hidden = withheld_from(line, user_id=reader_id, in_their_direct=in_direct)
    interaction = line.interaction
    return LineSummary(
        id=line.id,
        sender=line_sender_name(line),
        text=None if hidden or line.is_deleted else line.text,
        at=line.created_at.isoformat(),
        question_state=interaction.state.value if interaction else None,
        answer=dict(interaction.answer) if interaction and interaction.answer else None,
        awaits=(
            str(interaction.awaits.user_id or interaction.awaits.contact_id)
            if interaction and interaction.awaits
            else None
        ),
    )


async def _sees_contacts(deps: BaseAgentContext) -> bool:
    async with _uow() as uow:
        return await pod_members.pod_member_holds(
            uow, deps.pod_id, deps.user_id, Permissions.CONTACT_READ
        )


async def channel_post(
    ctx: RunContext[BaseAgentContext], request: ChannelPostRequest
) -> ChannelPostResponse:
    """Write to a member of the pod who is not in this conversation, or in a
    channel.

    With `to`, it goes to their own channel in Lemma, and on to the chat app
    they last used or their email. With `channel_id`, it is a line in that
    channel. It does **not** pause your turn, and an answer never comes back
    as a tool result. When you need answers, set `wants_answer` on each, send
    them all, then **finish your turn**: you get a fresh one here once the last
    person answers, and `channel_read(asked_by_me=True)` shows what they said.

    To answer the person you are already talking to, just reply.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return ChannelPostResponse(success=False, error="Only inside a pod.")
    if refused := _refused_to_outsiders(deps):
        return ChannelPostResponse(success=False, error=refused)
    if (request.to is None) == (request.channel_id is None):
        return ChannelPostResponse(
            success=False, error="Give exactly one of `to` or `channel_id`."
        )
    if request.channel_id is not None:
        return await _post_in_channel(deps, request)
    recipient = await direct_messages.resolve_recipient(
        pod_id=deps.pod_id, reference=request.to or ""
    )
    if recipient is None:
        return ChannelPostResponse(
            success=False,
            error=(
                f"No member of this pod matches '{request.to}'. It takes a pod "
                "member id, user id, or email address -- a name will not "
                "resolve. Call list_pod_members to look them up."
            ),
        )
    sent = await direct_messages.send_direct_message(
        pod_id=deps.pod_id,
        recipient_user_id=recipient,
        text=request.text,
        actor_user_id=deps.user_id,
        actor_agent_id=effective_agent_id(deps.workload_id, pod_id=deps.pod_id),
        agent_name=agent_display_name(deps.agent_display_name or deps.agent_name),
        origin_conversation_id=deps.conversation_id,
        origin_run_id=deps.agent_run_id,
        origin_surface_id=deps.surface_id,
        wants_answer=request.wants_answer,
        # A retried worker job replays this exact call: the same key makes it
        # the same line, not a second one.
        key=(
            f"{deps.agent_run_id}:{ctx.tool_call_id}"
            if deps.agent_run_id and ctx.tool_call_id
            else None
        ),
        platform=request.platform.value if request.platform else None,
    )
    if sent.delivered_via:
        said = f"Sent on {sent.delivered_via}, and in their channel in Lemma."
    else:
        said = f"In their channel in Lemma. {sent.reason or ''}".strip()
    if request.wants_answer:
        said += (
            " They have not answered yet. Once you have sent everything you "
            "need, end your turn -- you get a fresh one when everyone has replied."
        )
    return ChannelPostResponse(
        success=True,
        message=said,
        message_id=sent.message_id,
        channel_id=sent.channel_id,
        delivered_via=sent.delivered_via,
    )


async def _post_in_channel(
    deps: BaseAgentContext, request: ChannelPostRequest
) -> ChannelPostResponse:
    assert request.channel_id is not None
    sees_contacts = await _sees_contacts(deps)
    async with _uow() as uow:
        channel = await channel_reads.member_may_read(
            uow,
            pod_id=deps.pod_id,
            channel_id=request.channel_id,
            user_id=deps.user_id,
            sees_contacts=sees_contacts,
        )
        if channel is None:
            return ChannelPostResponse(success=False, error="No such channel here.")
        if channel.kind is ChannelKind.CONTACT:
            return ChannelPostResponse(
                success=False,
                error=(
                    "A contact's channel is written in by members: tell the "
                    "person who looks after them instead."
                ),
            )
        line = await post(
            uow,
            channel_id=channel.id,
            sender=Sender.agent(
                effective_agent_id(deps.workload_id, pod_id=deps.pod_id),
                on_behalf_of=Principal.member(deps.user_id),
                display_name=agent_display_name(
                    deps.agent_display_name or deps.agent_name
                ),
            ),
            text=request.text,
            conversation_id=deps.conversation_id,
            agent_run_id=deps.agent_run_id,
        )
        await uow.commit()
    return ChannelPostResponse(
        success=True,
        message="Posted in the channel.",
        message_id=line.id if line else None,
        channel_id=channel.id,
    )


async def channel_read(
    ctx: RunContext[BaseAgentContext], request: ChannelReadRequest
) -> ChannelReadResponse:
    """Read a channel's latest lines -- or, with `asked_by_me`, the questions
    this conversation put to people and their answers.

    Call `asked_by_me` when a turn opens saying they have answered -- not in a
    loop, and not to check on people who have not answered: you are told when
    there is something to read.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return ChannelReadResponse(success=False, error="Only inside a pod.")
    if refused := _refused_to_outsiders(deps):
        return ChannelReadResponse(success=False, error=refused)
    if request.asked_by_me:
        async with _uow() as uow:
            asked = await _asked_by(uow, deps)
        lines = [
            _summary(line, reader_id=deps.user_id, in_direct=True)
            for line in asked[-request.limit :]
        ]
        waiting = sum(1 for line in lines if line.question_state == "OPEN")
        return ChannelReadResponse(
            success=True,
            lines=lines,
            message=f"{len(lines) - waiting} answered, {waiting} still waiting.",
        )
    if request.channel_id is None:
        return ChannelReadResponse(
            success=False, error="Name a channel, or ask for `asked_by_me`."
        )
    sees_contacts = await _sees_contacts(deps)
    async with _uow() as uow:
        channel = await channel_reads.member_may_read(
            uow,
            pod_id=deps.pod_id,
            channel_id=request.channel_id,
            user_id=deps.user_id,
            sees_contacts=sees_contacts,
        )
        if channel is None:
            return ChannelReadResponse(success=False, error="No such channel here.")
        lines = await channel_reads.recent_lines(
            uow, channel_id=channel.id, limit=request.limit
        )
    in_direct = channel.kind is ChannelKind.DIRECT
    return ChannelReadResponse(
        success=True,
        lines=[
            _summary(line, reader_id=deps.user_id, in_direct=in_direct)
            for line in lines
            if not line.is_deleted
        ],
    )


async def _asked_by(
    uow: SqlAlchemyUnitOfWork, deps: BaseAgentContext
) -> list[ChannelMessage]:
    return await channel_questions.questions_under(
        uow, prefix=f"msg:{deps.conversation_id}:"
    )


async def channel_search(
    ctx: RunContext[BaseAgentContext], request: ChannelSearchRequest
) -> ChannelSearchResponse:
    """Find lines in the channels you can read that contain every word."""
    deps = ctx.deps
    if deps.pod_id is None:
        return ChannelSearchResponse(success=False, error="Only inside a pod.")
    if refused := _refused_to_outsiders(deps):
        return ChannelSearchResponse(success=False, error=refused)
    sees_contacts = await _sees_contacts(deps)
    async with _uow() as uow:
        channels = await channel_reads.channels_of_member(
            uow, pod_id=deps.pod_id, user_id=deps.user_id, sees_contacts=sees_contacts
        )
        kinds = {channel.id: channel.kind for channel, _ in channels}
        found = await channel_reads.search_lines(
            uow, channel_ids=list(kinds), words=request.query, limit=request.limit
        )
    return ChannelSearchResponse(
        success=True,
        lines=[
            _summary(
                line,
                reader_id=deps.user_id,
                in_direct=kinds.get(line.channel_id) is ChannelKind.DIRECT,
            )
            for line in found
            if not withheld_from(
                line,
                user_id=deps.user_id,
                in_their_direct=kinds.get(line.channel_id) is ChannelKind.DIRECT,
            )
        ],
    )


async def channel_list(
    ctx: RunContext[BaseAgentContext], request: ChannelListRequest
) -> ChannelListResponse:
    """The pod's channels you can read, most recently active first, with the
    outside places each is fed from."""
    deps = ctx.deps
    if deps.pod_id is None:
        return ChannelListResponse(success=False, error="Only inside a pod.")
    if refused := _refused_to_outsiders(deps):
        return ChannelListResponse(success=False, error=refused)
    try:
        kind = ChannelKind(request.kind.upper()) if request.kind else None
    except ValueError:
        return ChannelListResponse(
            success=False, error="kind is DIRECT, CONTACT or GROUP."
        )
    sees_contacts = await _sees_contacts(deps)
    async with _uow() as uow:
        channels = await channel_reads.channels_of_member(
            uow,
            pod_id=deps.pod_id,
            user_id=deps.user_id,
            sees_contacts=sees_contacts,
            kind=kind,
        )
    return ChannelListResponse(
        success=True,
        channels=[
            ChannelSummary(
                id=channel.id,
                kind=channel.kind.value,
                title=channel.title,
                places=[
                    f"{source.platform}: {source.title}"
                    if source.title
                    else source.platform
                    for source in sources
                ],
            )
            for channel, sources in channels
        ],
    )


async def ask_a_person(
    ctx: RunContext[BaseAgentContext], request: AskAPersonRequest
) -> AskAPersonResponse:
    """Call a person from the pod into this conversation.

    For when the person you are answering needs something only a member can
    give. Their channel is flagged as waiting on a person, and the member who
    looks after them is told; they will step in and reply here themselves.
    Tell the person someone will get back to them -- do not promise when.
    """
    deps = ctx.deps
    if deps.pod_id is None or deps.conversation_id is None:
        return AskAPersonResponse(success=False, error="Only in a conversation.")
    if not run_audience(deps).answers_outsiders:
        return AskAPersonResponse(
            success=False,
            error="You are talking to a member already: answer them yourself.",
        )
    if deps.keeper_asking:
        # The member who typed this is the person it would call in.
        return AskAPersonResponse(
            success=False,
            error=(
                "This was written in Lemma by the member who looks after this "
                "conversation: answer them here yourself."
            ),
        )
    flagged = await direct_messages.ask_a_person(
        pod_id=deps.pod_id,
        conversation_id=deps.conversation_id,
        keeper_user_id=deps.user_id,
        reason=request.reason[:MAX_OUTSIDER_MESSAGE_CHARS],
    )
    if not flagged:
        return AskAPersonResponse(
            success=False, error="There is nobody to call into this conversation."
        )
    return AskAPersonResponse(
        success=True,
        message="Done: a member has been asked to step in. Tell them someone will reply.",
    )


async def list_pod_members(
    ctx: RunContext[BaseAgentContext], request: ListPodMembersRequest
) -> ListPodMembersResponse:
    """Look up who is in this pod, to find who to write to.

    `channel_post` needs an id or an exact email address for `to` — a name won't resolve
    — so start here whenever you know a person by name. Each result carries a
    `to` value to pass straight through.

    Also tells you `reachable_on`: the channels that can carry a message to each
    person right now. Read it before setting `channel_post`'s `platform`, and
    don't ask for one that isn't listed — the send will be refused rather than
    rerouted.

    Searches names and email addresses; omit `search` to list everyone.
    """
    deps = ctx.deps

    if deps.pod_id is None:
        return ListPodMembersResponse(
            success=False, error="list_pod_members is only available inside a pod."
        )
    if deps.audience.answers_outsiders:
        # The directory is read as the member who looks after this run, and
        # everything in it could be repeated to the stranger asking. Nobody
        # listed there could be messaged from here anyway.
        return ListPodMembersResponse(
            success=False,
            error=(
                "You are talking to somebody outside the pod, so its members are "
                "not yours to list. When they need a person, use `ask_a_person`."
            ),
        )

    page = await pod_directory.list_pod_members(
        pod_id=deps.pod_id,
        requester_user_id=deps.user_id,
        search=request.search,
        limit=request.limit,
    )
    if page is None:
        return ListPodMembersResponse(
            success=False, error="You do not have access to this pod's member list."
        )

    members = page.members
    if not members:
        detail = (
            f" Nothing matched '{request.search}'."
            if request.search
            else " This pod has no members."
        )
        return ListPodMembersResponse(
            success=True, message=f"No members found.{detail}"
        )

    reach = await _reachable_on(members, pod_id=deps.pod_id, deps=deps)
    return ListPodMembersResponse(
        success=True,
        members=[
            PodMemberSummary(
                to=member.to,
                name=member.name,
                email=member.email,
                role=member.role,
                is_you=member.is_you,
                # `user_id` is the key reachability is computed against and is
                # deliberately not offered to the model: `to` is the value it
                # copies, and a second id would only invite it to pass the wrong
                # one.
                reachable_on=reach.get(member.user_id, []),
            )
            for member in members
        ],
        total_matched=page.total_matched,
        truncated=page.truncated,
        message=(
            f"{page.total_matched} match(es); showing {len(members)}. "
            "Pass a member's `to` value to channel_post."
            if page.truncated
            else f"{len(members)} member(s). Pass a `to` value to channel_post."
        ),
    )


async def _reachable_on(
    members: tuple[pod_directory.PodDirectoryMember, ...],
    *,
    pod_id: UUID,
    deps: BaseAgentContext,
) -> dict[UUID, list[str]]:
    """Which channels this agent can reach each listed person on, right now.

    Two lookups rather than one, because they are two modules' answers: `pod`
    says who is in the pod, `agent_surfaces` says who has a surface this agent
    can send on. They were one function in the composition root, which is what
    made the mixture untypeable.

    Only the page that is actually returned: reachability costs queries per
    surface, and answering it for members nobody will see is work spent on rows
    the model never reads.
    """
    recipients = {member.user_id: member.email for member in members if member.user_id}
    if not recipients:
        return {}
    return await direct_messages.reachable_channels(
        pod_id=pod_id,
        recipients=recipients,
        # Whose reach to report. Same sentinel handling as channel_post: the pod
        # assistant is not a row in `agents`, and its surfaces are the ones with
        # the pod's own id on them.
        actor_agent_id=effective_agent_id(deps.workload_id, pod_id=pod_id),
    )


messaging_toolset = FunctionToolset[BaseAgentContext](
    tools=[
        channel_post,
        channel_read,
        channel_search,
        channel_list,
        list_pod_members,
        list_teammates,
        ask_teammate,
        ask_a_person,
    ]
)
