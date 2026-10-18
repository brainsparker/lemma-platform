from __future__ import annotations

import json
from types import SimpleNamespace

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import channels


class Recorder:
    """Every SDK call made, by name, with what it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"called": name}

        return call


def _client(channels_fake: Recorder, sources_fake: Recorder | None = None):
    class FakeClient:
        def pod(self, pod_id):
            return SimpleNamespace(
                channels=channels_fake, sources=sources_fake or Recorder()
            )

    return FakeClient()


def test_list_reads_the_kind_in_any_case(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    result = runner.invoke(app, ["channels", "list", "--kind", "group", "--json"])

    assert result.exit_code == 0, result.stderr
    [(name, _, kwargs)] = fake.calls
    assert name == "list"
    assert kwargs["kind"] == "GROUP"


def test_an_unknown_kind_is_refused_before_anything_is_asked(
    runner, patch_run, json_state
):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    result = runner.invoke(app, ["channels", "list", "--kind", "room"])

    assert result.exit_code != 0
    assert fake.calls == []


def test_post_names_the_agents_and_members_it_mentions(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    result = runner.invoke(
        app,
        [
            "channels",
            "post",
            "ch-1",
            "Can you summarise?",
            "--mention-agent",
            "agent-1",
            "--mention",
            "user-2",
            "--thread",
            "line-9",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stderr
    [(name, args, kwargs)] = fake.calls
    assert (name, args) == ("post", ("ch-1", "Can you summarise?"))
    assert kwargs["mentions"] == [
        {"kind": "AGENT", "id": "agent-1"},
        {"kind": "MEMBER", "id": "user-2"},
    ]
    assert kwargs["thread_root_id"] == "line-9"
    assert kwargs["members_only"] is False


def test_an_answer_needs_something_to_say(runner, patch_run, json_state):
    fake = Recorder()
    patch_run(channels, client=_client(fake), state=json_state)

    refused = runner.invoke(app, ["channels", "answer", "ch-1", "line-1"])
    answered = runner.invoke(
        app,
        ["channels", "answer", "ch-1", "line-1", "-d", json.dumps({"seats": 3})],
    )

    assert refused.exit_code != 0
    assert answered.exit_code == 0, answered.stderr
    assert fake.calls == [("answer", ("ch-1", "line-1", {"seats": 3}), {})]


def test_a_source_is_switched_through_the_sources_resource(
    runner, patch_run, json_state
):
    sources = Recorder()
    patch_run(channels, client=_client(Recorder(), sources), state=json_state)

    result = runner.invoke(
        app, ["channels", "source", "set", "src-1", "--no-answers-outsiders", "--json"]
    )

    assert result.exit_code == 0, result.stderr
    assert sources.calls == [
        ("update", ("src-1",), {"answers_outsiders": False, "take_over": False})
    ]
