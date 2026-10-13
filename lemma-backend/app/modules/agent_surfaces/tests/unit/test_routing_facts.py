"""The typed reading of an event's admission facts and reply target."""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.routing_facts import (
    InboundRoutingFacts,
    ReplyTarget,
)


def _event(**overrides: object) -> ParsedInboundSurfaceEvent:
    fields: dict[str, object] = {
        "platform": "SLACK",
        "conversation_type": ConversationType.EXTERNAL_GROUP,
        "external_thread_id": "1.0",
        "message_text": "hi",
    }
    fields.update(overrides)
    return ParsedInboundSurfaceEvent.model_validate(fields)


def test_admission_facts_are_read_once_and_typed():
    facts = InboundRoutingFacts.of(
        _event(
            metadata={
                "is_thread_reply": True,
                "event_type": "message",
                "mentioned_user_ids": ["U1", 2],
                "reply_to_user_id": 42,
            }
        )
    )
    assert facts.is_thread_reply is True
    assert facts.event_type == "message"
    assert facts.mentioned_user_ids == frozenset({"U1", "2"})
    assert facts.reply_to_user_id == "42"
    assert facts.has_bot_command is False


def test_an_event_without_facts_reads_as_none_of_them():
    facts = InboundRoutingFacts.of(_event())
    assert facts == InboundRoutingFacts()


def test_a_reply_target_names_the_number_a_message_arrived_on():
    target = ReplyTarget.of(_event(reply_target={"phone_number_id": "pn-1"}))
    assert target.phone_number_id == "pn-1"
    assert ReplyTarget.of(None) == ReplyTarget()
