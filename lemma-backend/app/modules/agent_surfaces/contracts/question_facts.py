"""What the surfaces module announces when an asking conversation is owed no
more answers.

Emitted in the transaction that closes the *last* open question one
conversation put to people -- answered, or cancelled -- so the agent module's
subscriber brings the conversation back with its item committed alongside the
answer: an answer can no longer be recorded and then lost before anyone acted
on it. Waiting for the last answer rather than the first keeps a four-person
round of questions to one turn instead of four.

A submodule rather than ``contracts/__init__``: it pulls in the work runtime.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.work import Fact, pod_namespace


class AskedQuestionsSettled(Fact):
    conversation_id: UUID
    pod_id: UUID

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


__all__ = ["AskedQuestionsSettled"]
