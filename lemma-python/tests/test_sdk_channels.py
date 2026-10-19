from __future__ import annotations

from typing import Any
from uuid import uuid4

from lemma_sdk.openapi_client.api.channels import (
    channel_member_leave,
    channel_message_post,
    channel_read,
    channel_update,
)
from lemma_sdk.resources.channels import PodChannels

POD = uuid4()
CHANNEL = uuid4()


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        self.calls.append((endpoint, path_args, kwargs))
        return None


def _sent(call: tuple[Any, tuple[Any, ...], dict[str, Any]]) -> dict[str, Any]:
    """The body as it would go over the wire, through the generated model."""
    _, _, kwargs = call
    return kwargs["body_model"].from_dict(kwargs["body"]).to_dict()


def test_an_update_sends_only_the_fields_named_and_none_clears() -> None:
    transport = RecordingTransport()

    PodChannels(transport, pod_id=POD).update(CHANNEL, topic=None, archived=True)

    [call] = transport.calls
    assert call[0] is channel_update
    assert call[1] == (POD, CHANNEL)
    assert _sent(call) == {"topic": None, "archived": True}


def test_switching_a_contacts_agent_is_a_patch_of_agents_answer() -> None:
    transport = RecordingTransport()
    channels = PodChannels(transport, pod_id=POD)

    channels.set_agent_answers(CHANNEL, False)
    channels.set_agent_answers(CHANNEL, True, hand_back_after_hours=6)

    assert [call[0] for call in transport.calls] == [channel_update, channel_update]
    assert _sent(transport.calls[0]) == {"agents_answer": False}
    assert _sent(transport.calls[1]) == {
        "agents_answer": True,
        "hand_back_after_hours": 6,
    }


def test_read_is_marked_up_to_a_seq() -> None:
    transport = RecordingTransport()

    PodChannels(transport, pod_id=POD).mark_read(CHANNEL, 42)

    [call] = transport.calls
    assert call[0] is channel_read
    assert _sent(call) == {"seq": 42}


def test_mentions_go_out_as_typed_parts() -> None:
    transport = RecordingTransport()
    agent = str(uuid4())

    PodChannels(transport, pod_id=POD).post(
        CHANNEL,
        "Can you summarise?",
        mentions=[{"kind": "AGENT", "id": agent}],
        client_message_id="draft-1",
    )

    [call] = transport.calls
    assert call[0] is channel_message_post
    sent = _sent(call)
    assert sent["parts"] == [{"type": "mention", "kind": "AGENT", "id": agent}]
    assert sent["client_message_id"] == "draft-1"


def test_leaving_is_one_call_about_the_caller() -> None:
    transport = RecordingTransport()

    PodChannels(transport, pod_id=POD).leave(CHANNEL)

    [(endpoint, path_args, _)] = transport.calls
    assert endpoint is channel_member_leave
    assert path_args == (POD, CHANNEL)
