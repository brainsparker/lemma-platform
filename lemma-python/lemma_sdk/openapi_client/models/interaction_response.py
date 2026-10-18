from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from dateutil.parser import isoparse

from ..models.interaction_kind import InteractionKind
from ..models.interaction_state import InteractionState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.interaction_response_answer_type_0 import (
        InteractionResponseAnswerType0,
    )
    from ..models.interaction_response_question import InteractionResponseQuestion
    from ..models.person_ref import PersonRef


T = TypeVar("T", bound="InteractionResponse")


@_attrs_define
class InteractionResponse:
    """
    Attributes:
        kind (InteractionKind): What a line asks of the one person it waits on.
        question (InteractionResponseQuestion): What is asked: a `prompt`, and `choices` or a `form` where the asking
            side offers them.
        state (InteractionState):
        answer (InteractionResponseAnswerType0 | None | Unset):
        answered_at (datetime.datetime | None | Unset):
        answered_by (None | PersonRef | Unset):
        awaits (None | PersonRef | Unset): The one person who may answer it.
    """

    kind: InteractionKind
    question: InteractionResponseQuestion
    state: InteractionState
    answer: InteractionResponseAnswerType0 | None | Unset = UNSET
    answered_at: datetime.datetime | None | Unset = UNSET
    answered_by: None | PersonRef | Unset = UNSET
    awaits: None | PersonRef | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.interaction_response_answer_type_0 import (
            InteractionResponseAnswerType0,
        )
        from ..models.person_ref import PersonRef

        kind = self.kind.value

        question = self.question.to_dict()

        state = self.state.value

        answer: dict[str, Any] | None | Unset
        if isinstance(self.answer, Unset):
            answer = UNSET
        elif isinstance(self.answer, InteractionResponseAnswerType0):
            answer = self.answer.to_dict()
        else:
            answer = self.answer

        answered_at: None | str | Unset
        if isinstance(self.answered_at, Unset):
            answered_at = UNSET
        elif isinstance(self.answered_at, datetime.datetime):
            answered_at = self.answered_at.isoformat()
        else:
            answered_at = self.answered_at

        answered_by: dict[str, Any] | None | Unset
        if isinstance(self.answered_by, Unset):
            answered_by = UNSET
        elif isinstance(self.answered_by, PersonRef):
            answered_by = self.answered_by.to_dict()
        else:
            answered_by = self.answered_by

        awaits: dict[str, Any] | None | Unset
        if isinstance(self.awaits, Unset):
            awaits = UNSET
        elif isinstance(self.awaits, PersonRef):
            awaits = self.awaits.to_dict()
        else:
            awaits = self.awaits

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "question": question,
                "state": state,
            }
        )
        if answer is not UNSET:
            field_dict["answer"] = answer
        if answered_at is not UNSET:
            field_dict["answered_at"] = answered_at
        if answered_by is not UNSET:
            field_dict["answered_by"] = answered_by
        if awaits is not UNSET:
            field_dict["awaits"] = awaits

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.interaction_response_answer_type_0 import (
            InteractionResponseAnswerType0,
        )
        from ..models.interaction_response_question import InteractionResponseQuestion
        from ..models.person_ref import PersonRef

        d = dict(src_dict)
        kind = InteractionKind(d.pop("kind"))

        question = InteractionResponseQuestion.from_dict(d.pop("question"))

        state = InteractionState(d.pop("state"))

        def _parse_answer(
            data: object,
        ) -> InteractionResponseAnswerType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                answer_type_0 = InteractionResponseAnswerType0.from_dict(data)

                return answer_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(InteractionResponseAnswerType0 | None | Unset, data)

        answer = _parse_answer(d.pop("answer", UNSET))

        def _parse_answered_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                answered_at_type_0 = isoparse(data)

                return answered_at_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(datetime.datetime | None | Unset, data)

        answered_at = _parse_answered_at(d.pop("answered_at", UNSET))

        def _parse_answered_by(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                answered_by_type_0 = PersonRef.from_dict(data)

                return answered_by_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        answered_by = _parse_answered_by(d.pop("answered_by", UNSET))

        def _parse_awaits(data: object) -> None | PersonRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                awaits_type_0 = PersonRef.from_dict(data)

                return awaits_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | PersonRef | Unset, data)

        awaits = _parse_awaits(d.pop("awaits", UNSET))

        interaction_response = cls(
            kind=kind,
            question=question,
            state=state,
            answer=answer,
            answered_at=answered_at,
            answered_by=answered_by,
            awaits=awaits,
        )

        interaction_response.additional_properties = d
        return interaction_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
