"""How far a member has read, with threads in it.

* A channel's unread lines are its timeline's: replies count on their thread.
* A mention is read where it was said -- in the timeline, or in its thread.
  Reading the timeline to its end leaves a reply naming the reader unread;
  reading that thread reads it.
* Replying in a thread reads that thread, not the channel.
* Joining a channel starts at its newest line; what named them before stays.
* A channel muted for now is not among the channels with something new.

Against Postgres, through the API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.channels.tests.e2e.channel_pod import Pod, group, say

pytestmark = pytest.mark.e2e


def _naming(person: dict) -> list[dict[str, str]]:
    return [{"type": "mention", "kind": "MEMBER", "id": person["id"], "name": "x"}]


async def _unread(pod: Pod, channel_id: str, who: dict) -> dict:
    found = await pod.client(who).get(f"{pod.base}/{channel_id}")
    assert found.status_code == 200, found.text
    return found.json()["unread"]


async def _read(pod: Pod, channel_id: str, who: dict, seq: int, **body) -> dict:
    read = await pod.client(who).post(
        f"{pod.base}/{channel_id}/read", json={"seq": seq, **body}
    )
    assert read.status_code == 200, read.text
    return read.json()


async def test_replies_count_on_their_thread_not_in_the_timeline(pod: Pod):
    crew = await group(pod, members=[pod.user["id"]])
    root = await say(pod, crew["id"], "Plan for Tuesday")
    for n in range(3):
        await say(pod, crew["id"], f"reply {n}", thread_root_id=root["id"])

    assert (await _unread(pod, crew["id"], pod.user))["lines"] == 1
    await _read(pod, crew["id"], pod.user, root["seq"])
    assert (await _unread(pod, crew["id"], pod.user))["lines"] == 0
    inbox = await pod.client(pod.user).get(f"/pods/{pod.id}/inbox")
    assert inbox.json()["totals"]["channels"] == 0, inbox.text

    await say(pod, crew["id"], "Next topic")
    assert (await _unread(pod, crew["id"], pod.user))["lines"] == 1


async def test_a_mention_in_a_thread_is_read_in_its_thread(pod: Pod):
    crew = await group(pod, members=[pod.user["id"]])
    root = await say(pod, crew["id"], "Plan for Tuesday")
    named = await say(
        pod,
        crew["id"],
        "can you take this?",
        thread_root_id=root["id"],
        parts=_naming(pod.user),
    )
    last = await say(pod, crew["id"], "Next topic")

    # The timeline read to its end: the reply naming them is not read.
    await _read(pod, crew["id"], pod.user, last["seq"])
    unread = await _unread(pod, crew["id"], pod.user)
    assert (unread["lines"], unread["mentions"]) == (0, 1)

    # Read in its thread, it is -- and the timeline's number stays.
    read = await _read(
        pod, crew["id"], pod.user, named["seq"], thread_root_id=root["id"]
    )
    assert read["read_seq"] == last["seq"]
    assert (await _unread(pod, crew["id"], pod.user))["mentions"] == 0


async def test_replying_in_a_thread_reads_the_thread_not_the_channel(pod: Pod):
    crew = await group(pod, members=[pod.user["id"]])
    root = await say(pod, crew["id"], "Plan for Tuesday")
    await say(pod, crew["id"], "see this", parts=_naming(pod.user))
    await say(
        pod,
        crew["id"],
        "and this",
        thread_root_id=root["id"],
        parts=_naming(pod.user),
    )
    before = await _unread(pod, crew["id"], pod.user)
    assert before["mentions"] == 2

    await say(pod, crew["id"], "on it", as_user=pod.user, thread_root_id=root["id"])

    after = await _unread(pod, crew["id"], pod.user)
    # The thread is read; the line naming them in the timeline is not.
    assert after["read_seq"] == before["read_seq"]
    assert after["mentions"] == 1
    assert after["lines"] == before["lines"]


async def test_joining_starts_at_the_newest_line(pod: Pod):
    town = await group(pod, title="Town square")
    for n in range(5):
        await say(pod, town["id"], f"history {n}")
    named = await say(pod, town["id"], "ask the user", parts=_naming(pod.user))

    joined = await pod.client(pod.user).post(f"{pod.base}/{town['id']}/members/me")
    assert joined.status_code == 200, joined.text
    unread = joined.json()["unread"]
    assert unread["lines"] == 0
    assert unread["read_seq"] == named["seq"]
    # Named before they came: still theirs to read.
    assert unread["mentions"] == 1

    await say(pod, town["id"], "welcome")
    assert (await _unread(pod, town["id"], pod.user))["lines"] == 1


async def test_a_channel_muted_for_now_is_not_counted_as_new(pod: Pod):
    crew = await group(pod, members=[pod.user["id"]])
    await say(pod, crew["id"], "news")
    member = pod.client(pod.user)
    inbox = await member.get(f"/pods/{pod.id}/inbox")
    assert inbox.json()["totals"]["channels"] == 1, inbox.text

    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    muted = await member.patch(
        f"{pod.base}/{crew['id']}/members/me", json={"muted_until": until}
    )
    assert muted.status_code == 200, muted.text
    inbox = await member.get(f"/pods/{pod.id}/inbox")
    assert inbox.json()["totals"]["channels"] == 0, inbox.text

    ended = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await member.patch(
        f"{pod.base}/{crew['id']}/members/me", json={"muted_until": ended}
    )
    inbox = await member.get(f"/pods/{pod.id}/inbox")
    assert inbox.json()["totals"]["channels"] == 1, inbox.text
