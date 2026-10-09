"""A member's files and rows are not posted where people outside the pod read.

A member's run in a group reads with that member's authority. Before, a table
it displayed went to the whole chat -- a Slack Connect channel's other company,
a group opened to strangers. Now the chat gets a note and a link that opens only
in Lemma, and the contents are never read for it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.contracts import DisplayResourceRequest, DisplayResourceType
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    ConversationType,
)
from app.modules.agent_surfaces.services import channel_exposure, egress_service
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    _delivering_adapter,
    _slack_event,
    _slack_surface,
    build_egress,
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio


def _channel_egress(monkeypatch, *, shared: bool, group: object | None = None):
    surface = _slack_surface()
    conversation_id = uuid4()
    event = _slack_event().model_copy(
        update={
            "is_dm": False,
            "conversation_type": ConversationType.EXTERNAL_GROUP,
            "external_channel_id": "C-SHARED",
            "metadata": {"is_ext_shared_channel": shared},
        }
    )
    link = AgentSurfaceConversationLink(
        surface_id=surface.id,
        conversation_id=conversation_id,
        platform="SLACK",
        external_channel_id="C-SHARED",
        external_thread_id=event.external_thread_id,
        external_user_id=event.sender_external_user_id,
        conversation_kind="CHANNEL",
        last_event=event.model_dump(mode="json"),
    )
    adapter = _delivering_adapter()
    egress = build_egress(adapter=adapter, surfaces=[surface], existing_link=link)
    egress.delivery.conversation_link_repository.get_by_conversation_id.return_value = (
        link
    )
    monkeypatch.setattr(
        channel_exposure,
        "SurfaceGroupRepository",
        lambda _session: SimpleNamespace(get=AsyncMock(return_value=group)),
    )
    return egress, adapter, conversation_id


@pytest.mark.parametrize(
    "request_",
    [
        DisplayResourceRequest(type=DisplayResourceType.TABLE, name="deals"),
        DisplayResourceRequest(type=DisplayResourceType.FILE, path="/me/payroll.csv"),
    ],
    ids=["table", "file"],
)
async def test_a_shared_channel_gets_a_link_not_the_contents(monkeypatch, request_):
    egress, adapter, conversation_id = _channel_egress(monkeypatch, shared=True)
    rows = AsyncMock()
    file_bytes = AsyncMock()
    monkeypatch.setattr(egress_service, "resolve_table_preview", rows)
    monkeypatch.setattr(egress_service, "resolve_pod_file_parts", file_bytes)

    sent = await egress.send_display_resource_for_conversation(
        conversation_id=conversation_id, request=request_, tool_call_id="t-1"
    )

    assert sent is True
    rows.assert_not_awaited()
    file_bytes.assert_not_awaited()
    adapter._render_resource.assert_awaited_once()
    note = adapter.send_message.await_args.kwargs["message"]
    assert "outside the pod" in note


async def test_a_group_opened_to_outsiders_is_treated_the_same(monkeypatch):
    group = SimpleNamespace(shared_externally=False, welcomes_outsiders=True)
    egress, _adapter, conversation_id = _channel_egress(
        monkeypatch, shared=False, group=group
    )
    rows = AsyncMock()
    monkeypatch.setattr(egress_service, "resolve_table_preview", rows)

    await egress.send_display_resource_for_conversation(
        conversation_id=conversation_id,
        request=DisplayResourceRequest(type=DisplayResourceType.TABLE, name="deals"),
    )

    rows.assert_not_awaited()


async def test_a_channel_of_members_still_gets_the_rows(monkeypatch):
    egress, _adapter, conversation_id = _channel_egress(monkeypatch, shared=False)
    rows = AsyncMock(return_value=None)
    monkeypatch.setattr(egress_service, "resolve_table_preview", rows)
    monkeypatch.setattr(egress_service, "apply_table_rows", lambda plan, _rows: plan)

    await egress.send_display_resource_for_conversation(
        conversation_id=conversation_id,
        request=DisplayResourceRequest(type=DisplayResourceType.TABLE, name="deals"),
    )

    rows.assert_awaited_once()
