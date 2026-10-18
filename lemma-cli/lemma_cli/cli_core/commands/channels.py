from __future__ import annotations

from typing import Any, Callable

import typer

from ..io import emit, to_plain
from ..payload import read_json
from ..sdk import pod_client
from ..state import run_with_client, state_from_ctx

app = typer.Typer(
    help="Channel commands: where a pod's members, contacts and agents talk."
)
source_app = typer.Typer(
    help="A group source's settings, and bringing the bot into a group."
)
app.add_typer(source_app, name="source")
app.add_typer(source_app, name="sources", hidden=True)

_KINDS = ("DIRECT", "CONTACT", "GROUP")
_NOTIFY = ("ALL", "MENTIONS", "NONE")


def _run(ctx: typer.Context, pod: str | None, call: Callable[[Any], Any]) -> None:
    """Call the pod's SDK and print what comes back."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx, lambda client, s: to_plain(call(pod_client(client, s, pod)))
    )
    if result is not None:
        emit(state, result)


def _choice(value: str | None, allowed: tuple[str, ...], flag: str) -> str | None:
    if value is None:
        return None
    upper = value.upper()
    if upper not in allowed:
        raise typer.BadParameter(f"{flag} is one of {', '.join(allowed)}.")
    return upper


@app.command("list")
def list_channels(
    ctx: typer.Context,
    kind: str | None = typer.Option(None, "--kind", help="DIRECT, CONTACT or GROUP."),
    archived: bool = typer.Option(False, "--archived", help="Archived ones instead."),
    contact: str | None = typer.Option(None, "--contact", help="Only this contact's."),
    limit: int = typer.Option(50, "--limit"),
    before: str | None = typer.Option(
        None, "--before", help="The previous page's next_before."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """The channels you can see, most recently active first, with what is new."""
    _run(
        ctx,
        pod,
        lambda p: p.channels.list(
            kind=_choice(kind, _KINDS, "--kind"),
            archived=archived,
            contact_id=contact,
            limit=limit,
            before=before,
        ),
    )


@app.command("get")
def get_channel(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """One channel: its sources, participants and your settings."""
    _run(ctx, pod, lambda p: p.channels.get(channel_id))


@app.command("create")
def create_channel(
    ctx: typer.Context,
    title: str = typer.Argument(..., help="What to call it."),
    topic: str | None = typer.Option(None, "--topic"),
    member: list[str] = typer.Option(
        [], "--member", help="A member's user id to add. Repeat for more."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Make a channel in Lemma, with you and the members named in it."""
    _run(ctx, pod, lambda p: p.channels.create(title, topic=topic, members=member))


@app.command("messages")
def list_messages(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    limit: int = typer.Option(50, "--limit"),
    before: str | None = typer.Option(
        None, "--before", help="The previous page's next_before."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """The timeline, oldest first; a thread's replies are counted on its line."""
    _run(
        ctx,
        pod,
        lambda p: p.channels.messages(channel_id, limit=limit, before=before),
    )


@app.command("thread")
def list_thread(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    message_id: str = typer.Argument(..., help="The thread's first line."),
    limit: int = typer.Option(50, "--limit"),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """A thread's replies, oldest first."""
    _run(ctx, pod, lambda p: p.channels.thread(channel_id, message_id, limit=limit))


@app.command("post")
def post_message(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    text: str = typer.Argument(..., help="What to say."),
    members_only: bool = typer.Option(
        False, "--members-only", help="A note to members: never sent outside."
    ),
    source: str | None = typer.Option(
        None, "--source", help="Which outside place it goes to (a source id)."
    ),
    thread: str | None = typer.Option(
        None, "--thread", help="Reply in the thread of this line."
    ),
    agent: list[str] = typer.Option(
        [], "--mention-agent", help="An agent id to name; it answers. Repeat for more."
    ),
    member: list[str] = typer.Option(
        [], "--mention", help="A member's user id to name. Repeat for more."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Write in a channel: to the place it last heard from, unless a note."""
    mentions = [{"kind": "AGENT", "id": a} for a in agent] + [
        {"kind": "MEMBER", "id": m} for m in member
    ]
    _run(
        ctx,
        pod,
        lambda p: p.channels.post(
            channel_id,
            text,
            members_only=members_only,
            source_id=source,
            thread_root_id=thread,
            mentions=mentions,
        ),
    )


@app.command("answer")
def answer_question(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    message_id: str = typer.Argument(..., help="The line asking you."),
    text: str | None = typer.Option(None, "--text", help="A written answer."),
    choice: str | None = typer.Option(None, "--choice", help="One of its choices."),
    decision: str | None = typer.Option(
        None, "--decision", help="APPROVE_ONCE or DENY, for an approval."
    ),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="The answer as JSON: a form's fields."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Answer what a line asks you."""
    answer: dict[str, Any] = read_json(json_payload, None) if json_payload else {}
    for key, value in (("text", text), ("choice", choice), ("decision", decision)):
        if value is not None:
            answer[key] = value
    if not answer:
        raise typer.BadParameter("Pass --text, --choice, --decision or --data.")
    _run(ctx, pod, lambda p: p.channels.answer(channel_id, message_id, answer))


@app.command("inbox")
def inbox(
    ctx: typer.Context,
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """What is new for you across the pod, and what is waiting on you."""
    _run(ctx, pod, lambda p: p.channels.inbox())


@app.command("search")
def search(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Every word must match."),
    limit: int = typer.Option(30, "--limit"),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Lines matching every word, newest first, in the channels you see."""
    _run(ctx, pod, lambda p: p.channels.search(query, limit=limit))


@app.command("read")
def mark_read(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Note that you have read the channel up to now."""
    _run(ctx, pod, lambda p: p.channels.mark_read(channel_id) or {"read": True})


@app.command("notify")
def set_notify(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="Channel id."),
    level: str = typer.Argument(..., help="ALL, MENTIONS or NONE."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """How you hear of this channel's lines outside the app."""
    chosen = _choice(level, _NOTIFY, "LEVEL")
    _run(ctx, pod, lambda p: p.channels.set_notify(channel_id, chosen))


@app.command("tell")
def tell(
    ctx: typer.Context,
    to: str = typer.Argument(..., help="A pod member id, user id or email."),
    text: str = typer.Argument(..., help="What to tell them."),
    title: str | None = typer.Option(None, "--title"),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Tell another member something, in their own channel."""
    _run(ctx, pod, lambda p: p.channels.notify(to, text, title=title))


@app.command("agent")
def switch_agent(
    ctx: typer.Context,
    channel_id: str = typer.Argument(..., help="A contact's channel id."),
    state: str = typer.Argument(..., help="on or off."),
    hand_back_after: int | None = typer.Option(
        None,
        "--hand-back-after",
        help="Hours a member who wrote to them holds them before the agent answers again.",
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Whether a contact's agent answers them. Off, only members do; on hands
    them back to the agent at once."""
    on = {"on": True, "off": False}.get(state.lower())
    if on is None:
        raise typer.BadParameter("STATE is on or off.")
    _run(
        ctx,
        pod,
        lambda p: p.channels.set_agent_answers(
            channel_id, on, hand_back_after_hours=hand_back_after
        ),
    )


@source_app.command("get")
def get_source(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="Source id, from the channel."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """A group source's settings: who answers its outsiders, and from what."""
    _run(ctx, pod, lambda p: p.sources.get(source_id))


@source_app.command("set")
def set_source(
    ctx: typer.Context,
    source_id: str = typer.Argument(..., help="Source id, from the channel."),
    answers_outsiders: bool | None = typer.Option(
        None,
        "--answers-outsiders/--no-answers-outsiders",
        help="Answer people outside the pod there, or stop.",
    ),
    take_over: bool = typer.Option(
        False, "--take-over", help="Answer for its outsiders yourself."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Switch outsiders on or off in one group, or take it over."""
    _run(
        ctx,
        pod,
        lambda p: p.sources.update(
            source_id, answers_outsiders=answers_outsiders, take_over=take_over
        ),
    )


@source_app.command("whatsapp-group")
def start_whatsapp_group(
    ctx: typer.Context,
    surface: str = typer.Argument(..., help="The WhatsApp surface's name."),
    title: str = typer.Argument(..., help="The group's subject."),
    no_outsiders: bool = typer.Option(
        False, "--no-outsiders", help="Do not answer people outside the pod."
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Start a WhatsApp group with the pod's bot in it: a new group channel."""
    _run(
        ctx,
        pod,
        lambda p: p.sources.start_whatsapp_group(
            surface, title, answers_outsiders=not no_outsiders
        ),
    )


@source_app.command("telegram-link")
def telegram_link(
    ctx: typer.Context,
    surface: str = typer.Argument(..., help="The Telegram surface's name."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """A one-use, hour-long link that adds the pod's Telegram bot to a group."""
    _run(ctx, pod, lambda p: p.sources.telegram_link(surface))
