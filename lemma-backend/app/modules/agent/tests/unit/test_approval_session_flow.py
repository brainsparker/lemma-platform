"""Unit tests for the request_approval session flow.

Covers the plumbing the authorizer's session-approval check relies on: the
denied-tool-result payload, the server's record of what a call was refused
for, and the APPROVE_FOR_SESSION resolution recording per-permission approvals
from that record -- for the person who gave them.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.authorization import session_approvals
from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID
from app.core.authorization.session_approvals import exact_command_permission_id
from app.core.domain.errors import DomainError
from app.modules.agent.services.approval_reconciliation import (
    record_session_approvals,
)
from app.modules.agent.tools.tool_errors import approval_error_result


def test_approval_error_result_carries_permission_ids_for_destructive_denial():
    exc = DomainError(
        "Missing permission datastore.table.delete",
        code="DESTRUCTIVE_ACTION_REQUIRES_APPROVAL",
        status_code=403,
        details={"permission_ids": ["datastore.table.delete"]},
    )

    result = approval_error_result(
        exc, tool_name="pod_delete_table", args={"table": "orders"}
    )

    assert result["needs_approval"] is True
    approval = result["approval"]
    assert approval["tool_name"] == "pod_delete_table"
    assert approval["reason_code"] == "DESTRUCTIVE_ACTION_REQUIRES_APPROVAL"
    assert approval["permission_ids"] == ["datastore.table.delete"]


def test_approval_error_result_without_details_still_flags_approval():
    exc = DomainError(
        "Missing permission folder.read",
        code="MISSING_WORKLOAD_RESOURCE_GRANT",
        status_code=403,
    )

    result = approval_error_result(exc, tool_name="pod_read_file", args={})

    assert result["needs_approval"] is True
    assert "permission_ids" not in result["approval"]


def test_non_approval_codes_do_not_flag():
    exc = DomainError("nope", code="INSUFFICIENT_PERMISSION", status_code=403)
    result = approval_error_result(exc, tool_name="pod_read_file", args={})
    assert "needs_approval" not in result


class _FakeCache:
    """The session-approval store's Redis, in memory: the real record/lookup
    functions run against it, so the keys they agree on are the real keys."""

    def __init__(self):
        self.data: dict[str, object] = {}

    async def set_json(self, suffix, value, ttl_seconds=None):
        self.data[suffix] = value

    async def get_json(self, suffix):
        return self.data.get(suffix)

    async def delete(self, suffix):
        self.data.pop(suffix, None)


@pytest.fixture
def store(monkeypatch) -> _FakeCache:
    cache = _FakeCache()
    monkeypatch.setattr(session_approvals, "_get_approval_cache", lambda: cache)
    return cache


def _conversation(*, agent_id=None):
    return SimpleNamespace(id=uuid4(), agent_id=agent_id, pod_id=uuid4())


async def _refuse(conversation, *, tool_name, args, permission_ids, actor=None):
    """What a tool does when the authorizer refuses it: the server's record."""
    await session_approvals.record_denial(
        session_id=str(conversation.id),
        workload_actor_id=actor
        or f"agent:{conversation.agent_id or conversation.pod_id}",
        tool_name=tool_name,
        args=args,
        permission_ids=permission_ids,
    )


async def _approved_for(conversation, *, person, permission_id, actor=None) -> bool:
    return await session_approvals.has_session_approval(
        session_id=str(conversation.id),
        person_user_id=person,
        workload_actor_id=actor
        or f"agent:{conversation.agent_id or conversation.pod_id}",
        permission_id=permission_id,
    )


@pytest.mark.asyncio
async def test_approve_for_session_records_each_permission_the_call_was_refused_for(
    store,
):
    conversation = _conversation(agent_id=uuid4())
    person = uuid4()
    args = {"table": "orders"}
    await _refuse(
        conversation,
        tool_name="pod_delete_table",
        args=args,
        permission_ids=["datastore.table.delete", "folder.delete"],
    )

    await record_session_approvals(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        pod_id=conversation.pod_id,
        tool_args={"tool_name": "pod_delete_table", "args": args},
        user_id=person,
    )

    # An exact-command entry beside the refused permissions, so a later
    # identical request_approval call can auto-run without re-prompting.
    for permission_id in (
        exact_command_permission_id("pod_delete_table", args),
        "datastore.table.delete",
        "folder.delete",
    ):
        assert await _approved_for(
            conversation, person=person, permission_id=permission_id
        )


@pytest.mark.asyncio
async def test_approve_for_session_records_what_was_refused_not_what_the_model_asked_for(
    store,
):
    """H6: the ``permission_ids`` in a ``request_approval`` are the model's
    words. A model steered by a poisoned page adds ``pod.delete`` to a request
    for something harmless; approving that "for the session" used to record it,
    and the agent could then delete the pod without asking again."""
    conversation = _conversation(agent_id=uuid4())
    person = uuid4()
    args = {"action": "delete", "table_name": "chores", "record_id": "r1"}
    await _refuse(
        conversation,
        tool_name="pod_write_record",
        args=args,
        permission_ids=["datastore.record.write"],
    )

    await record_session_approvals(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        pod_id=conversation.pod_id,
        tool_args={
            "tool_name": "pod_write_record",
            "args": args,
            "permission_ids": ["datastore.record.write", "pod.delete"],
        },
        user_id=person,
    )

    assert await _approved_for(
        conversation, person=person, permission_id="datastore.record.write"
    )
    assert not await _approved_for(
        conversation, person=person, permission_id="pod.delete"
    )


@pytest.mark.asyncio
async def test_a_request_nothing_refused_unlocks_nothing_but_itself(store):
    """The other half of H6: an ``exec_command`` the server never refused --
    there is no gate on it -- carrying ``permission_ids`` of its own gets only
    its exact-command key. One approval of ``echo hi`` is not ``pod.delete``."""
    conversation = _conversation()
    person = uuid4()
    args = {"cmd": "echo hi"}

    await record_session_approvals(
        conversation_id=conversation.id,
        agent_id=None,
        pod_id=conversation.pod_id,
        tool_args={
            "tool_name": "exec_command",
            "args": args,
            "permission_ids": ["pod.delete", "datastore.table.delete"],
        },
        user_id=person,
    )

    assert await _approved_for(
        conversation,
        person=person,
        permission_id=exact_command_permission_id("exec_command", args),
    )
    for permission_id in ("pod.delete", "datastore.table.delete"):
        assert not await _approved_for(
            conversation, person=person, permission_id=permission_id
        )


@pytest.mark.asyncio
async def test_approving_a_sandbox_command_for_the_session_approves_what_it_was_refused(
    store,
):
    """A ``lemma`` call in the sandbox is refused over HTTP, where the API
    records it for the session; approving the command for the session approves
    exactly that -- and not the ``pod.delete`` the model added -- then forgets
    the refusal, so a later one-time approval does not lend it again."""
    conversation = _conversation()
    person = uuid4()
    actor = f"agent:{conversation.pod_id}"
    key = {
        "session_id": str(conversation.id),
        "person_user_id": person,
        "workload_actor_id": actor,
    }
    claims = SimpleNamespace(
        session_id=str(conversation.id),
        invoked_by_user_id=person,
        actor_type=SimpleNamespace(value="AGENT"),
        actor_id=conversation.pod_id,
    )
    await session_approvals.record_delegated_refusal(
        claims,  # type: ignore[arg-type]
        code="DESTRUCTIVE_ACTION_REQUIRES_APPROVAL",
        details={"permission_ids": ["datastore.table.delete"]},
    )

    await record_session_approvals(
        conversation_id=conversation.id,
        agent_id=None,
        pod_id=conversation.pod_id,
        tool_args={
            "tool_name": "exec_command",
            "args": {"cmd": "lemma tables delete orders"},
            "permission_ids": ["datastore.table.delete", "pod.delete"],
        },
        user_id=person,
    )

    assert await _approved_for(
        conversation, person=person, permission_id="datastore.table.delete"
    )
    assert not await _approved_for(
        conversation, person=person, permission_id="pod.delete"
    )
    assert await session_approvals.session_refusals(**key) == frozenset()


@pytest.mark.asyncio
async def test_a_session_approval_covers_only_the_person_who_gave_it(store):
    """M5: in a group conversation the agent runs for whoever spoke last. The
    owner's "allow for this conversation" does not reach the run another member
    triggers, whose context carries that member as its person."""
    conversation = _conversation(agent_id=uuid4())
    owner, member = uuid4(), uuid4()
    args = {"table": "orders"}
    await _refuse(
        conversation,
        tool_name="pod_delete_table",
        args=args,
        permission_ids=["datastore.table.delete"],
    )

    await record_session_approvals(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        pod_id=conversation.pod_id,
        tool_args={"tool_name": "pod_delete_table", "args": args},
        user_id=owner,
    )

    assert await _approved_for(
        conversation, person=owner, permission_id="datastore.table.delete"
    )
    assert not await _approved_for(
        conversation, person=member, permission_id="datastore.table.delete"
    )


@pytest.mark.asyncio
async def test_a_refusal_recorded_with_nulls_is_found_by_a_request_without_them(
    store,
):
    """A pod tool records its refusal with its request model dumped whole; the
    model restating the call leaves the unset fields out. Same call."""
    conversation = _conversation(agent_id=uuid4())
    await _refuse(
        conversation,
        tool_name="pod_write_record",
        args={"action": "delete", "record_id": "r1", "data": None},
        permission_ids=["datastore.record.write"],
    )

    refused = await session_approvals.denied_permissions(
        session_id=str(conversation.id),
        workload_actor_id=f"agent:{conversation.agent_id}",
        tool_name="pod_write_record",
        args={"action": "delete", "record_id": "r1"},
    )

    assert refused == {"datastore.record.write"}


@pytest.mark.asyncio
async def test_refusals_of_one_call_accumulate(store):
    """The authorizer names one missing permission at a time, so one call is
    refused twice. The record keeps both, or the second approval would lend
    only the second and the call would stop at the first again."""
    conversation = _conversation(agent_id=uuid4())
    for permission_id in ("datastore.table.read", "datastore.record.write"):
        await _refuse(
            conversation,
            tool_name="pod_write_record",
            args={"record_id": "r1"},
            permission_ids=[permission_id],
        )

    refused = await session_approvals.denied_permissions(
        session_id=str(conversation.id),
        workload_actor_id=f"agent:{conversation.agent_id}",
        tool_name="pod_write_record",
        args={"record_id": "r1"},
    )

    assert refused == {"datastore.table.read", "datastore.record.write"}


@pytest.mark.asyncio
async def test_approve_for_session_keys_the_pod_assistant_by_its_pod(monkeypatch):
    """The pod's assistant has no agent on its conversation, and its runs carry
    its own id -- which is its pod's. Recorded under ``DEFAULT_POD_AGENT_ID``,
    which no run carries, an approval it was given was never found."""
    recorded: list[dict] = []

    async def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(
        "app.core.authorization.session_approvals.record_session_approval",
        fake_record,
    )
    conversation = SimpleNamespace(id=uuid4(), agent_id=None, pod_id=uuid4())

    await record_session_approvals(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        pod_id=conversation.pod_id,
        tool_args={"tool_name": "pod_delete_table", "args": {}},
        user_id=uuid4(),
    )

    assert recorded[0]["workload_actor_id"] == f"agent:{conversation.pod_id}"
    assert recorded[0]["workload_actor_id"] != f"agent:{DEFAULT_POD_AGENT_ID}"


@pytest.mark.asyncio
async def test_approve_for_session_without_tool_name_records_nothing(monkeypatch):
    async def fail_record(**kwargs):
        raise AssertionError("nothing to key an exact-command approval on")

    monkeypatch.setattr(
        "app.core.authorization.session_approvals.record_session_approval",
        fail_record,
    )
    conversation = SimpleNamespace(id=uuid4(), agent_id=None, pod_id=uuid4())

    await record_session_approvals(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        pod_id=conversation.pod_id,
        tool_args={"permission_ids": ["datastore.table.delete"]},
        user_id=uuid4(),
    )


class TestTheSecondDenialOfTheSameCall:
    """DEV-ACCESS-002: approve -> still denied -> approve -> still denied.

    Reading a table needs two permissions and the authorizer stops at the first
    missing one, so the agent hits a second denial for the *same* call. The
    exact-command key is tool-name-plus-args, so that second attempt matches the
    session approval recorded by the first and reaches the auto-approve path --
    which used to execute the tool and return, swallowing the request without
    ever recording the new permission. The agent hit the same denial on its own
    next attempt, forever.

    The fast path now *requires* every permission the server refused the call
    for, rather than recording any. A call refused for something not yet
    approved falls through to the pause, so the person is asked once and
    `record_session_approvals` writes it with a human behind it.
    """

    @staticmethod
    def _deps(conversation_id, user_id):
        return SimpleNamespace(
            conversation_id=conversation_id,
            user_id=user_id,
            workload_id=None,
            agent_run_id=uuid4(),
            supports_pause_signal=True,
        )

    @staticmethod
    def _patch(
        monkeypatch,
        *,
        granted: set[str],
        executed: list[str],
        refused: frozenset[str] = frozenset(),
        approver=None,
    ):
        async def has_session_approval(
            *, session_id, person_user_id, workload_actor_id, permission_id
        ):
            if approver is not None and person_user_id != approver:
                return False
            return permission_id in granted

        async def denied_permissions(**kwargs):
            return refused

        async def record_session_approval(**kwargs):  # pragma: no cover
            raise AssertionError(
                "the auto-approve path must never mint a grant; only a resolved "
                f"approval may do that (tried to record {kwargs['permission_id']!r})"
            )

        async def execute_as_user(self, *, deps, tool_name, args):
            executed.append(tool_name)
            return {"ok": True}

        monkeypatch.setattr(
            "app.core.authorization.session_approvals.has_session_approval",
            has_session_approval,
        )
        monkeypatch.setattr(
            "app.modules.agent.tools.approval_denials.denied_permissions",
            denied_permissions,
        )
        monkeypatch.setattr(
            "app.core.authorization.session_approvals.record_session_approval",
            record_session_approval,
        )
        monkeypatch.setattr(
            "app.modules.agent.tools.approval.executor.ApprovalExecutor.execute_as_user",
            execute_as_user,
        )

    @pytest.mark.asyncio
    async def test_a_new_permission_falls_through_to_the_pause(self, monkeypatch):
        """The loop, closed: the request is no longer swallowed."""
        from app.modules.agent.tools.user_interaction import (
            pydantic_adapter as approvals,
        )

        args = {"table": "orders"}
        executed: list[str] = []
        # The user approved this exact call earlier, but has never been asked
        # about the permission the second denial named.
        self._patch(
            monkeypatch,
            granted={exact_command_permission_id("pod_get_records", args)},
            refused=frozenset({"datastore.record.read"}),
            executed=executed,
        )

        result = await approvals._run_if_exact_match_already_approved(
            deps=self._deps(uuid4(), uuid4()),
            tool_name="pod_get_records",
            args=args,
        )

        assert result is None, "must pause and ask, not silently self-approve"
        assert executed == [], "nothing may run before the user has been asked"

    @pytest.mark.asyncio
    async def test_a_fully_granted_call_still_auto_executes(self, monkeypatch):
        """Once everything is granted, the repeat runs without re-prompting."""
        from app.modules.agent.tools.user_interaction import (
            pydantic_adapter as approvals,
        )

        args = {"table": "orders"}
        executed: list[str] = []
        self._patch(
            monkeypatch,
            granted={
                exact_command_permission_id("pod_get_records", args),
                "datastore.record.read",
            },
            refused=frozenset({"datastore.record.read"}),
            executed=executed,
        )

        result = await approvals._run_if_exact_match_already_approved(
            deps=self._deps(uuid4(), uuid4()),
            tool_name="pod_get_records",
            args=args,
        )

        assert result is not None and result.executed is True
        assert executed == ["pod_get_records"]

    @pytest.mark.asyncio
    async def test_another_member_s_approval_does_not_auto_run_this_one_s_call(
        self, monkeypatch
    ):
        """M5 on the fast path: the exact call was approved for the
        conversation -- by someone else. This run's person is asked."""
        from app.modules.agent.tools.user_interaction import (
            pydantic_adapter as approvals,
        )

        args = {"cmd": "ls"}
        executed: list[str] = []
        self._patch(
            monkeypatch,
            granted={exact_command_permission_id("exec_command", args)},
            executed=executed,
            approver=uuid4(),
        )

        result = await approvals._run_if_exact_match_already_approved(
            deps=self._deps(uuid4(), uuid4()), tool_name="exec_command", args=args
        )

        assert result is None
        assert executed == []

    @pytest.mark.asyncio
    async def test_a_call_carrying_no_permissions_still_auto_executes(
        self, monkeypatch
    ):
        """exec_command has no structured permission -- the exact-command key is
        the whole grant, and that path must keep working."""
        from app.modules.agent.tools.user_interaction import (
            pydantic_adapter as approvals,
        )

        args = {"cmd": "ls"}
        executed: list[str] = []
        self._patch(
            monkeypatch,
            granted={exact_command_permission_id("exec_command", args)},
            executed=executed,
        )

        result = await approvals._run_if_exact_match_already_approved(
            deps=self._deps(uuid4(), uuid4()), tool_name="exec_command", args=args
        )

        assert result is not None and result.executed is True
        assert executed == ["exec_command"]
